"""Centralized error types with teaching hints (CLAUDE.md 踩坑 #37).

All REST (vCenter namespace-management) and Kubernetes (Supervisor / TKC)
failures are translated into VksApiError at the connection layer, so users
and agents see actionable guidance instead of raw tracebacks.

Everything this skill raises deliberately derives from :class:`VksError`, and
that is the single entry the MCP layer's ``_safe_error`` allowlists. Raising a
bare ``RuntimeError`` for an authored message silently discards it — the agent
sees ``"RuntimeError: operation failed."`` instead.
"""
from __future__ import annotations

from typing import Optional

# Transient gateway errors — safe to retry once for read-only requests.
TRANSIENT_STATUS_CODES = (502, 503, 504)


class VksError(RuntimeError):
    """Base for every failure this skill raises on purpose.

    Subclasses RuntimeError so existing ``except RuntimeError`` paths and
    tests keep working. This is the type the MCP layer's ``_safe_error``
    passes through verbatim, so every message raised under it is written for
    an agent to read: name the corrected next step, and never interpolate a
    raw exception, a resolved host:port or a certificate subject — chain the
    cause instead and let the server log carry the detail.

    Put the remedy before any interpolated value: the message is capped at
    300 characters on the way out, so whatever comes last is what truncates.
    """


class VksApiError(VksError):
    """API call failed — carries the HTTP status code and a teaching hint."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class VksSafetyError(VksError):
    """A guard refused the operation; nothing was changed.

    Distinct from :class:`VksApiError` — no API call failed. The remedy is to
    satisfy the precondition the message names (empty the namespace, drain
    the cluster, pass ``force=True``), not to retry.
    """


def cause_summary(exc: BaseException) -> str:
    """Describe ``exc`` as a nested cause, quoting it only if we authored it.

    Applies ``_safe_error``'s rule one layer down: a :class:`VksError` message
    was written for an agent to read, so it is quotable; anything else is raw
    upstream text that can carry response bodies, hostnames or certificate
    subjects, so only its class name is used.

    ``ConfigError`` is quoted for the same reason it is on ``_safe_error``'s
    passthrough list: its text *is* the remedy. A missing password surfaces
    here through the delete guards' credential read, and reducing it to a bare
    class name would answer the family's most common first-run failure with a
    name the operator cannot act on. Imported inside the function to keep this
    module a leaf — ``config`` runs .env loading at import time.
    """
    from vmware_vks.config import ConfigError

    authored = isinstance(exc, (VksError, ConfigError))
    return str(exc) if authored else type(exc).__name__


def connection_failure_message(exc: BaseException, target: str = "") -> str:
    """Authored message for a transport failure, safe to show an agent.

    The raw text of a TLS failure quotes the certificate subject and a DNS
    failure quotes the host it could not resolve, so only the exception's
    class name is used — enough to tell TLS from DNS from timeout, which is
    the distinction that picks the remedy. The full detail reaches the server
    log through the chained cause.
    """
    where = f"target '{target}'" if target else "the configured vCenter"
    return (
        f"Cannot reach {where} ({type(exc).__name__}). Run 'vmware-vks check' "
        f"to diagnose. If this vCenter uses a self-signed certificate, set "
        f"verify_ssl: false on that target in ~/.vmware-vks/config.yaml."
    )


def rest_hint_for_status(status_code: Optional[int]) -> str:
    """Teaching hint for a vCenter REST (namespace-management) status code.

    401 and 403 used to share this branch and both said "Permission denied —
    verify the account has Workload Management permissions". On the VCF 9.1
    hardware pass every REST call was 401 (the SOAP session key was being sent
    as a REST session id), and that sentence sent the operator to re-check a
    password and a role that were both already correct.

    They are different answers to different questions. A 401 reaching this
    function has already survived one silent re-login, so the account's
    credentials are known good and the *session* is the thing being refused.
    A 403 means the account authenticated and lacks the privilege — that one
    really is Workload Management permissions, and still says so.
    """
    if status_code == 404:
        return (
            "Resource not found — run 'vmware-vks namespace list' to find "
            "the exact name."
        )
    if status_code == 401:
        return (
            "The vCenter REST session was refused even after re-authenticating, "
            "so this is not a password or a role — run 'vmware-vks check', and "
            "confirm nothing between here and vCenter (a proxy or gateway) is "
            "stripping the vmware-api-session-id header."
        )
    if status_code == 403:
        return (
            "Permission denied — verify the account has Workload Management "
            "permissions on this vCenter."
        )
    if status_code in TRANSIENT_STATUS_CODES:
        return (
            "vCenter/Supervisor REST endpoint not ready (transient gateway "
            "error) — wait a moment and retry."
        )
    return "Run 'vmware-vks check' to verify connectivity and configuration."


def translate_k8s_api_exception(
    exc: Exception,
    resource: str = "",
    namespace: str = "",
    kind: str = "TKC",
) -> VksApiError:
    """Translate a kubernetes.client ApiException into a VksApiError.

    Adds teaching hints per status code (404 → list command, 401/403 →
    permissions, 5xx → Supervisor not ready). Returns the error; caller
    decides to raise (keeps ``raise ... from exc`` chains at call sites).
    """
    status = getattr(exc, "status", None)
    reason = getattr(exc, "reason", None) or str(exc)

    if status == 404:
        msg = (
            f"{kind} '{resource}' not found in namespace '{namespace}' — "
            "run list_tkc_clusters to see available clusters."
        )
    elif status == 401:
        # The bearer token from POST /wcp/login was refused, so this *is* about
        # the SSO credentials it was minted from. Kept apart from 403 for the
        # same reason as in rest_hint_for_status: 403 is the account being
        # short a privilege, and telling someone to re-check a working password
        # is a dead end.
        msg = (
            f"Supervisor K8s API rejected the bearer token ({status}) for {kind} "
            f"'{resource}' in namespace '{namespace}' — check vCenter SSO "
            "credentials; run 'vmware-vks preflight-auth' to retest the login."
        )
    elif status == 403:
        msg = (
            f"Supervisor K8s API denied access ({status}) for {kind} "
            f"'{resource}' in namespace '{namespace}' — the account "
            "authenticated but lacks the privilege; it needs Workload "
            "Management permissions and RBAC on that namespace."
        )
    elif status in TRANSIENT_STATUS_CODES:
        msg = (
            f"Supervisor K8s API not ready ({status}) while accessing {kind} "
            f"'{resource}' in namespace '{namespace}' — the Supervisor may "
            "still be configuring; wait a moment and retry."
        )
    else:
        msg = (
            f"Supervisor K8s API call failed for {kind} '{resource}' in "
            f"namespace '{namespace}': {reason}"
        )
    return VksApiError(msg, status_code=status)
