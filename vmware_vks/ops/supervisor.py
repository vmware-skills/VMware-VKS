"""Supervisor layer operations (read-only).

Uses the vSphere Automation REST API (``/api``) via urllib, authenticated with
a session id from :mod:`vmware_vks.rest_session` — **not** the pyVmomi SOAP
session key, which ``/api`` does not recognise (see that module).
All functions take (si: ServiceInstance) as first argument.
"""
from __future__ import annotations

import json
import logging
import socket
import ssl
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

from vmware_policy import paginated, sanitize

from vmware_vks.connection import get_target_config, get_verify_ssl
from vmware_vks.errors import (
    TRANSIENT_STATUS_CODES,
    VksApiError,
    connection_failure_message,
    rest_hint_for_status,
)
from vmware_vks.rest_session import (
    get_rest_session_id,
    invalidate_rest_session_for_si,
    vcenter_host,
)

_log = logging.getLogger("vmware-vks.ops.supervisor")

_MIN_VERSION = (8, 0, 0)

# Default REST request timeout in seconds — prevents indefinite hangs when
# the vCenter REST endpoint is unreachable or slow. Override with the
# ``VMWARE_VKS_REST_TIMEOUT`` env var.
_REST_TIMEOUT = 30


def _target_name(si: ServiceInstance) -> str:
    """Config target name behind this connection, or "" if it is unknown.

    Used to name the target in a connection-failure message instead of the
    resolved host — the operator edits the config entry, not the hostname.
    """
    target = get_target_config(si)
    return target.name if target is not None else ""


def _build_ssl_context(si: ServiceInstance) -> ssl.SSLContext:
    """Build an SSL context that mirrors the pyVmomi connection's trust config.

    The verify_ssl flag is stashed by the connection manager in a module-level
    dict keyed by id(si) (see connection.get_verify_ssl). When True we use the
    default verifying context; when False (self-signed / lab certificates) we
    disable hostname and cert verification.
    """
    verify_ssl = get_verify_ssl(si)
    ctx = ssl.create_default_context()
    if not verify_ssl:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _rest_request(
    si: ServiceInstance,
    method: str,
    path: str,
    body: dict | None = None,
) -> Any:
    """Authenticated REST request against the vSphere Automation API.

    Handles GET/POST/PATCH/PUT/DELETE with uniform SSL verification and
    timeout behaviour. Returns parsed JSON on success; returns ``None`` when
    the response body is empty (e.g. DELETE).

    Authentication is a session id from ``POST /api/session``, fetched inside
    the loop so a re-login takes effect on the retry. It used to be
    ``si.content.sessionManager.currentSession.key`` — the SOAP session's key,
    which ``/api`` has never issued and always rejects (see
    :mod:`vmware_vks.rest_session`).

    Errors are centrally translated into VksApiError with a teaching hint
    (踩坑 #37 — no raw raise_for_status-style tracebacks reach the user).

    Two retries, kept apart because they answer different questions:

    * **401** — the session id was refused. Drop it, log in again, replay the
      request **once**. Safe for a write too: a request that was rejected at
      the session check never reached the resource.
    * **502/503/504** — transient gateway, GETs only, because a write that
      timed out may well have landed.
    """
    host = vcenter_host(si)
    url = f"https://{host}/api{path}"
    ctx = _build_ssl_context(si)

    data = json.dumps(body).encode() if body is not None else None
    transient_attempts = 2 if method == "GET" else 1
    transient_used = 0
    auth_retried = False

    while True:
        headers = {"vmware-api-session-id": get_rest_session_id(si)}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, context=ctx, timeout=_REST_TIMEOUT) as resp:  # nosec B310
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            # Hint before detail: the whole message is capped at 300 chars on
            # the way to an agent, so whatever comes last is what truncates —
            # and the response body is the expendable half.
            detail = sanitize(e.read().decode(errors="replace"), 300)
            error = VksApiError(
                f"REST {method} {path} failed ({e.code}). "
                f"{rest_hint_for_status(e.code)} Detail: {detail}",
                status_code=e.code,
            )
            error.__cause__ = e
            if e.code == 401 and not auth_retried:
                auth_retried = True
                invalidate_rest_session_for_si(si)
                continue
            if e.code in TRANSIENT_STATUS_CODES and transient_used + 1 < transient_attempts:
                transient_used += 1
                time.sleep(2)
                continue
            raise error
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
            # Authored message only: the raw text of a TLS failure quotes the
            # certificate subject and a DNS failure quotes the host, and this
            # error type passes through _safe_error verbatim.
            error = VksApiError(connection_failure_message(e, _target_name(si)))
            error.__cause__ = e
            if transient_used + 1 < transient_attempts:
                transient_used += 1
                time.sleep(2)
                continue
            raise error


def _rest_get(si: ServiceInstance, path: str) -> Any:
    """Authenticated REST GET using active pyVmomi session cookie."""
    return _rest_request(si, "GET", path)


def _rest_post(si: ServiceInstance, path: str, body: dict) -> Any:
    return _rest_request(si, "POST", path, body)


def _rest_patch(si: ServiceInstance, path: str, body: dict) -> Any:
    return _rest_request(si, "PATCH", path, body)


def _rest_delete(si: ServiceInstance, path: str) -> None:
    _rest_request(si, "DELETE", path)


def check_vks_compatibility(si: ServiceInstance) -> dict:
    """Check if this vCenter supports VKS (vSphere 8.x+)."""
    about = si.content.about
    version_str = about.version
    parts = tuple(int(x) for x in version_str.split(".")[:3])
    compatible = parts >= _MIN_VERSION

    try:
        clusters = _rest_get(si, "/vcenter/namespace-management/clusters")
    except Exception:
        clusters = []

    enabled = [c for c in clusters if c.get("config_status") == "RUNNING"]

    return {
        "compatible": compatible,
        "vcenter_version": version_str,
        "vcenter_build": about.build,
        "min_required_version": "8.0.0",
        "wcp_enabled_clusters": len(enabled),
        "wcp_clusters": [
            {"cluster": c.get("cluster"), "status": c.get("config_status")}
            for c in clusters
        ],
        "hint": None if compatible else "VKS requires vSphere 8.0+. Upgrade vCenter.",
    }


def get_supervisor_status(si: ServiceInstance, cluster_id: str) -> dict:
    """Get Supervisor Cluster status for a given compute cluster MoRef ID.

    Clusters.Info has no Kubernetes version field — the version comes from
    ``GET /api/vcenter/namespace-management/software/clusters/{cluster}``
    (field ``current_version``). That second call degrades gracefully:
    on failure, kubernetes_version is None and a kubernetes_version_hint
    explains why.
    """
    data = _rest_get(si, f"/vcenter/namespace-management/clusters/{cluster_id}")
    try:
        software = _rest_get(
            si, f"/vcenter/namespace-management/software/clusters/{cluster_id}"
        )
        k8s_version = (
            software.get("current_version") if isinstance(software, dict) else None
        )
        version_hint = None
    except Exception as e:  # graceful degradation — status fields still useful
        k8s_version = None
        version_hint = (
            f"Could not read Kubernetes version from the software endpoint: {e}. "
            "Verify Workload Management is fully configured on this cluster."
        )

    result = {
        "cluster_id": cluster_id,
        "config_status": data.get("config_status"),
        "kubernetes_status": data.get("kubernetes_status"),
        "api_server_cluster_endpoint": sanitize(data.get("api_server_cluster_endpoint", "")),
        "kubernetes_version": k8s_version,
        "network_provider": data.get("network_provider"),
    }
    if version_hint:
        return {**result, "kubernetes_version_hint": version_hint}
    return result


def list_supervisor_storage_policies(si: ServiceInstance) -> dict:
    """List vCenter storage policies (the policies users assign to Namespaces).

    Calls ``GET /api/vcenter/storage/policies`` — the
    ``namespace-management/storage/storage-policies`` path does not exist
    in the vSphere Automation API (it 404s on every call). Each item is
    {policy (policy ID), name (display name), description}.

    Returns the family list envelope; the endpoint returns every policy in one
    response, so ``total`` is the real count and nothing is truncated.
    """
    data = _rest_get(si, "/vcenter/storage/policies")
    rows = [
        {
            "policy": item.get("policy"),
            "name": sanitize(item.get("name", "") or ""),
            "description": sanitize(item.get("description", "") or "", max_len=1000),
        }
        for item in (data if isinstance(data, list) else [])
    ]
    return paginated(rows, total=len(rows))
