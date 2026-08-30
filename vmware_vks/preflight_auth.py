"""Live preflight for the Supervisor ``POST /wcp/login`` bearer-token flow.

Issue #13: ``wcp_login`` (the JSON ``session_id`` JWT flow that replaced the
old SOAP-session-key bearer token) is fully unit-tested with mocks but has
NEVER run against a real Supervisor. The unknowns are:

  * is the endpoint really ``/wcp/login``?
  * is the JSON field really ``session_id``?
  * does ``verify_ssl`` behave end-to-end?
  * does the resulting JWT actually authenticate a Supervisor K8s API call?

This module turns that validation into ONE command the user runs in their own
environment (``vmware-vks preflight-auth``). It performs the REAL flow against
the configured target and reports actionable, structured results.

DIAGNOSTIC CONTRACT: this code never raises for an operational failure — every
failure is a status line with a teaching message. A failed step is data, not a
traceback. Only genuine programmer errors would propagate (and the CLI's
``_cli_errors`` decorator catches the rest as a last resort).
"""
from __future__ import annotations

import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

# Trivial Supervisor K8s probe: version discovery. Anonymous-deny on most
# clusters, so a 200/403 with a valid bearer proves the token authenticates;
# 401 proves it does not. We use /api (not a namespaced list) so the probe
# does not depend on any namespace existing.
_K8S_PROBE_PATH = "/api"
_PROBE_TIMEOUT = 15


@dataclass(frozen=True)
class Step:
    """One preflight step result. Immutable — built once, never mutated."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class PreflightResult:
    """All steps for one target plus the overall verdict."""

    target: str
    steps: tuple[Step, ...]

    @property
    def passed(self) -> bool:
        return all(s.ok for s in self.steps)


def _connect_step(target_name: Optional[str]) -> tuple[Optional["ServiceInstance"], Step]:
    """Step 1: reach vCenter and open a pyVmomi session for the target."""
    try:
        from vmware_vks.config import load_config
        from vmware_vks.connection import ConnectionManager

        # load_config resolves the path itself (explicit arg, then the
        # environment, then the default); reading $VMWARE_VKS_CONFIG here was
        # one of the copies that let this command validate a different
        # Supervisor from the one `vmware-vks check` had just cleared.
        config = load_config()
        mgr = ConnectionManager(config)
        si = mgr.connect(target_name)
        host = si._stub.host.split(":")[0]
        return si, Step("vCenter reachable", True, f"connected to {host}")
    except Exception as e:  # diagnostic: degrade, never traceback
        return None, Step(
            "vCenter reachable",
            False,
            f"{e}. Check target name, host, credentials, and that the vCenter "
            "is reachable. Run 'vmware-vks check' for config diagnostics.",
        )


def _wcp_login_step(si: "ServiceInstance") -> tuple[Optional[str], tuple[Step, ...]]:
    """Step 2: REAL POST /wcp/login — report HTTP status + session_id parse.

    Returns (token, steps). On any failure the token is None and the steps
    carry a teaching message tailored to the failure mode.
    """
    from vmware_vks.connection import get_target_config, get_verify_ssl
    from vmware_vks.errors import VksApiError
    from vmware_vks.wcp_login import invalidate_wcp_token, wcp_login

    target = get_target_config(si)
    if target is None:
        return None, (
            Step(
                "POST /wcp/login",
                False,
                "No connection target metadata for this session — connect via "
                "ConnectionManager so login credentials are available.",
            ),
        )

    # Bypass the token cache so the preflight always hits the wire — a cached
    # token would mask an endpoint/field regression we are trying to detect.
    invalidate_wcp_token(target.host, target.username)
    url = f"https://{target.host}/wcp/login"
    try:
        token = wcp_login(
            target.host,
            target.username,
            target.password,
            verify_ssl=get_verify_ssl(si),
            target_name=target.name,
        )
    except VksApiError as e:
        code = e.status_code
        if code == 404:
            hint = (
                f"{url} returned 404 — the login endpoint differs on this "
                "Supervisor version. Capture the real path: run "
                f"'kubectl vsphere login' with -v=9, or curl -k -u USER {url} "
                "and adjacent paths (e.g. /wcp/login, /api/v1/namespaces), then "
                "file the observed path on issue #13."
            )
        elif code in (401, 403):
            hint = (
                f"{url} returned {code} — credentials reached the endpoint but "
                "were rejected. Verify vCenter SSO username/password and that "
                "the account has Workload Management permissions. (The endpoint "
                "path itself is correct if you see 401/403.)"
            )
        else:
            hint = str(e)
        return None, (Step("POST /wcp/login", False, hint),)

    reach = Step(
        "POST /wcp/login",
        True,
        f"{url} returned 200 — endpoint path confirmed on this Supervisor.",
    )
    parse = Step(
        "Parse session_id",
        True,
        "response contained a non-empty 'session_id' JWT bearer token.",
    )
    return token, (reach, parse)


def _k8s_probe_step(si: "ServiceInstance", token: str) -> Step:
    """Step 3: use the bearer token on a trivial Supervisor K8s API call.

    Hits the Supervisor API server's version-discovery endpoint (``/api``).
    A 401 means the JWT did NOT authenticate; 200/403 means it did (403 only
    means RBAC denies version discovery, which still proves authentication).
    """
    try:
        from vmware_vks.connection import get_verify_ssl
        from vmware_vks.k8s_connection import _resolve_supervisor_endpoint

        endpoint = _resolve_supervisor_endpoint(si)
    except Exception as e:
        return Step(
            "Supervisor K8s API",
            False,
            f"could not resolve the Supervisor API endpoint: {e}. Ensure "
            "Workload Management is enabled and a Supervisor is RUNNING.",
        )

    url = f"https://{endpoint}{_K8S_PROBE_PATH}"
    ctx = ssl.create_default_context()
    if not get_verify_ssl(si):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}"}, method="GET"
    )
    try:
        with urllib.request.urlopen(  # nosec B310
            req, context=ctx, timeout=_PROBE_TIMEOUT
        ) as resp:
            code = resp.status
    except urllib.error.HTTPError as e:
        code = e.code
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return Step(
            "Supervisor K8s API",
            False,
            f"{url} unreachable: {e}. The /wcp/login token was obtained but "
            "the Supervisor API endpoint could not be reached — check network "
            "routing to the Supervisor control plane.",
        )

    if code == 401:
        return Step(
            "Supervisor K8s API",
            False,
            f"{url} returned 401 — the /wcp/login JWT did NOT authenticate the "
            "K8s API. The 'session_id' field may not be the bearer token on "
            "this version; capture the kubeconfig that 'kubectl vsphere login' "
            "writes and compare the token, then note it on issue #13.",
        )
    return Step(
        "Supervisor K8s API",
        True,
        f"{url} returned {code} — bearer token authenticated the Supervisor "
        "K8s API (the /wcp/login JWT is a valid Kubernetes token).",
    )


def run_preflight_auth(target_name: Optional[str] = None) -> PreflightResult:
    """Run the full live wcp_login preflight for one target.

    Never raises for operational failures — returns a PreflightResult whose
    steps describe exactly which stage failed and how to fix it.
    """
    si, connect = _connect_step(target_name)
    label = target_name or "default"
    if si is None:
        return PreflightResult(label, (connect,))

    token, login_steps = _wcp_login_step(si)
    steps: tuple[Step, ...] = (connect, *login_steps)
    if token is None:
        return PreflightResult(label, steps)

    return PreflightResult(label, (*steps, _k8s_probe_step(si, token)))
