"""Supervisor (Workload Control Plane) login — the real bearer-token flow.

Supervisor and TKC Kubernetes API auth is NOT the pyVmomi SOAP session key.
The real flow (what ``kubectl vsphere login`` does) is:

    POST https://<supervisor-endpoint>/wcp/login   (HTTP Basic auth)
    → 200 {"session_id": "<jwt>"}

The login host is the Supervisor API endpoint, not vCenter: on a live
VCF 9.1 (2026-10-14, VKS#13) vCenter answers 404 and the Supervisor answers
200. See :func:`login_host`.

That JWT is the Kubernetes bearer token. Tokens are cached per
(host, username) with a conservative TTL and invalidated on 401.

This flow replaces the previous (incorrect) use of
``si.content.sessionManager.currentSession.key`` as the bearer token. The
endpoint, the ``session_id`` field and the bearer use were confirmed on a live
VCF 9.1 Supervisor (VKS#13); vSphere 7/8 have not been checked live.
"""
from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import logging
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

    from vmware_vks.config import TargetConfig

from vmware_vks.errors import VksApiError, connection_failure_message

_log = logging.getLogger("vmware-vks.wcp_login")

# Conservative TTL — Supervisor JWTs typically last ~10h; refresh at 8h.
_TOKEN_TTL_SECONDS = 8 * 3600

# (host, username) → (token, monotonic expiry). Tokens never touch disk.
_token_cache: dict[tuple[str, str], tuple[str, float]] = {}

_LOGIN_TIMEOUT = 30


def invalidate_wcp_token(host: str, username: str) -> None:
    """Drop the cached token for (host, username) — call on 401 responses."""
    _token_cache.pop((host, username), None)


def wcp_login(
    host: str,
    username: str,
    password: str,
    verify_ssl: bool = True,
    target_name: str = "",
    login_source: str = "",
) -> str:
    """Login to the Supervisor via POST /wcp/login and return the JWT.

    Uses HTTP Basic auth (vCenter SSO credentials). The returned
    ``session_id`` JWT is the Kubernetes bearer token. Cached per
    (host, username) for ~8h; call invalidate_wcp_token on 401.

    Args:
        target_name: config target this host came from. Named in the
            connection-failure message so the operator knows which entry in
            config.yaml to edit; the resolved host is deliberately not.
        login_source: how ``host`` was chosen (see :func:`login_host`). When
            given, a transport failure is reported as the Supervisor being
            unreachable, not vCenter.
    """
    key = (host, username)
    cached = _token_cache.get(key)
    if cached and time.monotonic() < cached[1]:
        return cached[0]

    url = f"https://{host}/wcp/login"
    ctx = ssl.create_default_context()
    if not verify_ssl:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    req = urllib.request.Request(
        url,
        data=b"",
        headers={"Authorization": f"Basic {creds}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=_LOGIN_TIMEOUT) as resp:  # nosec B310
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        invalidate_wcp_token(host, username)
        if e.code in (401, 403):
            raise VksApiError(
                f"Supervisor login failed for '{username}' (HTTP {e.code}) — the "
                f"vCenter SSO credentials were rejected, or the account lacks "
                f"Workload Management permissions. Verify the password in the "
                f"VMWARE_VKS_<TARGET>_PASSWORD environment variable, then run "
                f"'vmware-vks preflight-auth' to retest the login.",
                status_code=e.code,
            ) from e
        raise VksApiError(
            f"Supervisor login failed (HTTP {e.code}) at {url}. Run "
            f"check_vks_compatibility to confirm Workload Management is enabled "
            f"on this vCenter, or 'vmware-vks preflight-auth' to see the raw "
            f"/wcp/login response.",
            status_code=e.code,
        ) from e
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:
        # Authored message only. This error type passes through _safe_error
        # verbatim, and the raw text of a TLS failure quotes the certificate
        # subject while a DNS failure quotes the host it could not resolve.
        # HTTPException: http.client.InvalidURL for a host urllib cannot use,
        # and a connection dropped mid-response (RemoteDisconnected).
        if not login_source:
            raise VksApiError(
                f"Supervisor login failed. {connection_failure_message(e, target_name)}"
            ) from e
        where = f" for target '{target_name}'" if target_name else ""
        raise VksApiError(
            f"Supervisor login failed: cannot reach the Supervisor login address "
            f"(the {login_source}){where} ({type(e).__name__}). This is the "
            f"Supervisor, not vCenter — 'vmware-vks check' tests vCenter only. If the "
            f"Supervisor address is not reachable from this machine, set "
            f"supervisor_host for this target in ~/.vmware-vks/config.yaml. If its "
            f"certificate is not trusted here (e.g. signed by the vCenter VMCA), add "
            f"that CA to the trust store; verify_ssl: false is for isolated labs only."
        ) from e

    token = data.get("session_id") if isinstance(data, dict) else None
    if not token:
        raise VksApiError(
            f"Supervisor login to {url} succeeded but the response carried no "
            f"'session_id' field (keys: "
            f"{', '.join(sorted(data)) if isinstance(data, dict) else type(data).__name__}) "
            f"— unexpected /wcp/login response shape. Run 'vmware-vks preflight-auth' "
            f"to capture the raw response and report it with the vCenter build."
        )

    _token_cache[key] = (token, time.monotonic() + _TOKEN_TTL_SECONDS)
    return token


_HOSTNAME_OK = re.compile(r"^[A-Za-z0-9_](?:[A-Za-z0-9_.-]*[A-Za-z0-9_])?$")


def _parse_address(value: str, source: str) -> tuple[str, int | None]:
    """``(host, port)`` from a Supervisor address; the one parser for both uses.

    Scheme and path are dropped, one trailing dot of an FQDN is removed, bare
    IPv6 comes back bracketed. Anything that does not leave a usable host, or
    carries a port that is not 1-65535, raises a teaching VksApiError naming
    ``source`` (the value itself is not echoed: the published endpoint is data
    from vCenter).
    """
    text = value.strip().split("://", 1)[-1].split("/", 1)[0]
    try:
        ipaddress.ip_address(text)
    except ValueError:
        pass
    else:
        return (f"[{text}]" if ":" in text else text), None
    try:
        parts = urllib.parse.urlsplit(f"//{text}")
        hostname, port = parts.hostname or "", parts.port
    except ValueError:
        hostname, port = "", None
    if ":" in hostname:
        return f"[{hostname}]", port
    hostname = hostname[:-1] if hostname.endswith(".") else hostname
    if (
        not _HOSTNAME_OK.match(hostname)
        or ".." in hostname
        or any(c.isspace() for c in text)
        or port == 0
    ):
        raise VksApiError(
            f"The Supervisor address from the {source} is not a usable host "
            f"(expected an address such as 10.0.0.5, sup.example.com, "
            f"sup.example.com:6443 or [fd00::5]; write an internationalised name "
            f"in its xn-- form). Set supervisor_host for this target in "
            f"config.yaml, or run get_supervisor_status to see what vCenter publishes."
        )
    return hostname, port


def supervisor_address(value: str, source: str) -> str:
    """The Supervisor API address for Kubernetes calls: host, and port if given."""
    host, port = _parse_address(value, source)
    return f"{host}:{port}" if port else host


def _host_only(endpoint: str, source: str) -> str:
    """The login host: the same parse, port dropped (/wcp/login is on 443)."""
    return _parse_address(endpoint, source)[0]


SOURCE_CONFIG = "supervisor_host in config.yaml"
SOURCE_PUBLISHED = "Supervisor endpoint published by vCenter"


def login_host(si: "ServiceInstance", target: "TargetConfig") -> tuple[str, str]:
    """Where ``POST /wcp/login`` goes for this target, and how that was decided.

    The target's ``supervisor_host`` when set; otherwise the Supervisor API
    endpoint vCenter publishes. The port is dropped: the published endpoint
    may carry the Kubernetes port (6443), while /wcp/login is served on 443.
    Raises VksApiError when the address cannot be resolved or is unusable.
    """
    if target.supervisor_host:
        return _host_only(target.supervisor_host, SOURCE_CONFIG), SOURCE_CONFIG
    from vmware_vks.k8s_connection import _resolve_supervisor_endpoint

    return _host_only(_resolve_supervisor_endpoint(si), SOURCE_PUBLISHED), SOURCE_PUBLISHED


def get_wcp_token(si: "ServiceInstance") -> str:
    """Get a Supervisor bearer token for the target behind this connection.

    Pulls host/username/password from the connection-manager side store
    (see connection.get_target_config — 踩坑 #32 pattern) and honours the
    target's verify_ssl flag.
    """
    from vmware_vks.connection import get_target_config, get_verify_ssl

    target = get_target_config(si)
    if target is None:
        raise VksApiError(
            "No connection target metadata for this session — this ServiceInstance "
            "was not opened by ConnectionManager, so the Supervisor login "
            "credentials are unavailable. Connect via "
            "vmware_vks.connection.ConnectionManager, and run 'vmware-vks check' "
            "to verify the target resolves from config.yaml."
        )
    host, source = login_host(si, target)
    return wcp_login(
        host,
        target.username,
        target.password,
        verify_ssl=get_verify_ssl(si),
        target_name=target.name,
        login_source=source,
    )


def invalidate_wcp_token_for_si(si: "ServiceInstance") -> None:
    """Invalidate the cached token for the target behind this connection."""
    from vmware_vks.connection import get_target_config

    target = get_target_config(si)
    if target is None:
        return
    try:
        host, _ = login_host(si, target)
    except VksApiError:
        # Nothing could have been cached under a host that does not resolve.
        return
    invalidate_wcp_token(host, target.username)
