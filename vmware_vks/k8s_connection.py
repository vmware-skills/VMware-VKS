"""Layer 2: kubernetes Python client connection to Supervisor K8s API endpoint.

Kubeconfig is built from the active pyVmomi session and vCenter REST info.
Used for TKC CR lifecycle (create/get/delete/scale/upgrade).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

from vmware_vks.errors import VksApiError, VksError

_log = logging.getLogger("vmware-vks.k8s_connection")

# NOTE: the vCenter-host helper lives in rest_session.vcenter_host — the
# duplicate that used to live here was dead code and has been removed.

# Per-host cache of the resolved Supervisor API endpoint. Each TKC op builds
# the kubeconfig at least twice (version probe + CustomObjectsApi), and each
# build otherwise re-runs two vCenter REST round-trips (cluster list + detail).
# Endpoint is keyed by vCenter host so the per-(host,user) token flow and the
# version cache in ops/tkc stay aligned. Cleared by invalidate_endpoint_for_si.
_endpoint_cache: dict[str, str] = {}


def _si_host(si: ServiceInstance) -> str:
    """vCenter host key for the endpoint cache (matches tkc._version_cache key)."""
    return getattr(getattr(si, "_stub", None), "host", "default")


def invalidate_endpoint_for_si(si: ServiceInstance) -> None:
    """Drop the cached Supervisor endpoint for this connection's host."""
    _endpoint_cache.pop(_si_host(si), None)


def _resolve_supervisor_endpoint(si: ServiceInstance) -> str:
    """Resolve (and cache per host) the Supervisor API endpoint.

    Runs the two vCenter REST round-trips (cluster list + cluster detail) only
    on a cache miss; subsequent kubeconfig builds for the same host reuse it.
    """
    host = _si_host(si)
    cached = _endpoint_cache.get(host)
    if cached:
        return cached

    from vmware_vks.ops.supervisor import _rest_get

    try:
        clusters = _rest_get(si, "/vcenter/namespace-management/clusters")
        running = [c for c in clusters if c.get("config_status") == "RUNNING"]
        if not running:
            states = ", ".join(
                f"{c.get('cluster')}={c.get('config_status')}" for c in clusters
            ) or "none"
            raise VksApiError(
                f"No Supervisor cluster is in config_status RUNNING. Run "
                f"check_vks_compatibility to confirm this vCenter supports VKS, "
                f"then get_supervisor_status to watch the cluster come up; retry "
                f"once one reports RUNNING. Found: {states}."
            )
        cluster_data = _rest_get(
            si,
            f"/vcenter/namespace-management/clusters/{running[0]['cluster']}"
        )
        api_endpoint = cluster_data.get("api_server_cluster_endpoint", "")
        if not api_endpoint:
            raise VksApiError(
                f"Supervisor cluster '{running[0]['cluster']}' reports RUNNING but "
                f"published no api_server_cluster_endpoint — its control plane is "
                f"still coming up. Run get_supervisor_status to check, then retry."
            )
    except VksError:
        # Already authored for an agent (including the two above, and the REST
        # layer's translated errors) — re-wrapping would bury the specific
        # remedy under a generic one.
        raise
    except Exception as e:
        raise VksApiError(
            f"Could not retrieve the Supervisor API endpoint from vCenter "
            f"({type(e).__name__}). Run 'vmware-vks check' to verify the vCenter "
            f"connection, then check_vks_compatibility to confirm Workload "
            f"Management is enabled."
        ) from e

    _endpoint_cache[host] = api_endpoint
    return api_endpoint


def _build_supervisor_kubeconfig(si: ServiceInstance, namespace: str) -> dict[str, Any]:
    """Build kubeconfig as a dict for the Supervisor API endpoint.

    Uses the Supervisor JWT from POST /wcp/login as the bearer token (the
    pyVmomi SOAP session key is NOT a valid K8s token — see wcp_login) and
    reuses the per-host-cached cluster API endpoint.
    """
    api_endpoint = _resolve_supervisor_endpoint(si)

    from vmware_vks.wcp_login import get_wcp_token

    token = get_wcp_token(si)

    # Honour the verify_ssl flag stashed by the connection manager. When True,
    # the kubernetes client validates the Supervisor cert against the system CA
    # bundle (matching supervisor._build_ssl_context). Only self-signed/lab
    # setups (verify_ssl=false) skip verification.
    from vmware_vks.connection import get_verify_ssl

    skip_tls = not get_verify_ssl(si)

    return {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [{
            "name": "supervisor",
            "cluster": {
                "server": f"https://{api_endpoint}",
                "insecure-skip-tls-verify": skip_tls,
            }
        }],
        "users": [{"name": "vsphere-user", "user": {"token": token}}],
        "contexts": [{
            "name": "supervisor-context",
            "context": {
                "cluster": "supervisor",
                "user": "vsphere-user",
                "namespace": namespace,
            }
        }],
        "current-context": "supervisor-context",
    }


def translate_k8s_error(
    si: ServiceInstance,
    exc: Exception,
    resource: str = "",
    namespace: str = "",
    kind: str = "TKC",
):
    """Translate a kubernetes ApiException into a teaching VksApiError.

    Also invalidates the cached /wcp/login token on 401 so the next call
    re-authenticates instead of replaying an expired JWT.
    """
    from vmware_vks.errors import translate_k8s_api_exception

    if getattr(exc, "status", None) == 401:
        from vmware_vks.wcp_login import invalidate_wcp_token_for_si

        invalidate_wcp_token_for_si(si)
    return translate_k8s_api_exception(
        exc, resource=resource, namespace=namespace, kind=kind
    )


def get_supervisor_kubeconfig_str(si: ServiceInstance, namespace: str) -> str:
    """Build a kubeconfig YAML string for the Supervisor API endpoint.

    Used by CLI/MCP `kubeconfig get` to export to a user-chosen path.
    For in-process kubernetes client use, prefer get_k8s_client which
    keeps the bearer token in memory only.
    """
    import yaml as _yaml
    return _yaml.dump(_build_supervisor_kubeconfig(si, namespace))


def get_k8s_client(si: ServiceInstance, namespace: str):
    """Get a kubernetes ApiClient connected to the Supervisor namespace.

    Loads kubeconfig from an in-memory dict via load_kube_config_from_dict —
    the bearer token never touches disk, eliminating the temp-file TOCTOU
    window of the previous implementation.

    Caller MUST close the client when done (use as context manager or call .close()).
    """
    import kubernetes as k8s

    cfg_dict = _build_supervisor_kubeconfig(si, namespace)
    client_cfg = k8s.client.Configuration()
    k8s.config.load_kube_config_from_dict(
        config_dict=cfg_dict, client_configuration=client_cfg
    )
    return k8s.client.ApiClient(configuration=client_cfg)
