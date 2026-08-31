"""Namespace lifecycle operations.

All write operations require confirmed=True.
delete_namespace has an additional guard: rejects if TKC clusters exist inside.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

from vmware_policy import paginated, sanitize

from vmware_vks.errors import VksSafetyError, cause_summary
from vmware_vks.ops.supervisor import _rest_delete, _rest_get, _rest_patch, _rest_post

_log = logging.getLogger("vmware-vks.ops.namespace")


def _list_tkc_in_namespace(si: ServiceInstance, namespace: str) -> list[str]:
    """Return TKC cluster names in namespace. Used as delete guard.

    Fails CLOSED: if the TKC list cannot be retrieved, raise instead of
    returning [] — otherwise an API outage would silently let the delete
    guard pass and orphan running clusters (mirrors tkc workload guard).
    """
    from vmware_vks.ops.tkc import list_tkc_clusters
    try:
        result = list_tkc_clusters(si, namespace=namespace)
    except Exception as e:
        _log.warning("Could not verify TKC clusters in '%s': %s", namespace, e)
        raise VksSafetyError(
            f"Cannot verify TKC clusters in '{namespace}' — the delete is "
            f"refused rather than risk orphaning them. Run 'vmware-vks check' "
            f"to diagnose the connection, then list_tkc_clusters to confirm the "
            f"namespace is empty. Cause: {cause_summary(e)}"
        ) from e
    return [c["name"] for c in result.get("items", [])]


def list_namespaces(si: ServiceInstance) -> dict:
    """List all vSphere Namespaces.

    Returns the family list envelope. The endpoint returns the whole
    collection in one response, so ``total`` is the real namespace count and
    ``truncated`` is always False — the agent is told the listing is complete
    rather than left to guess (VMware-AIops issue #31).

    An empty result is annotated, because on this endpoint zero has two
    meanings. ``/api/vcenter/namespaces/instances`` answers ``200 []`` on a
    vCenter with no Workload Management at all, exactly as it does on a healthy
    Supervisor that simply has no namespaces yet (verified against a live
    vCenter, round 3). Returning the bare empty envelope was faithful and
    useless: an agent reads "there are no namespaces here" and moves on, when
    the true statement is "there is no Supervisor here". Its three sibling tools
    already say so with a teaching error; this one stayed silent.
    """
    data = _rest_get(si, "/vcenter/namespaces/instances")
    rows = [
        {
            "namespace": sanitize(item.get("namespace", "")),
            "config_status": item.get("config_status"),
            "description": sanitize(item.get("description", ""), max_len=1000),
        }
        for item in (data if isinstance(data, list) else [])
    ]
    extra = {} if rows else {"empty_reason": _why_no_namespaces(si)}
    return paginated(rows, total=len(rows), **extra)


def _why_no_namespaces(si: ServiceInstance) -> str:
    """Say which kind of zero this is. One extra REST call, only when empty."""
    from vmware_vks.ops.supervisor import check_vks_compatibility

    try:
        compat = check_vks_compatibility(si)
    except Exception as exc:  # noqa: BLE001 — an unusable probe must not mask the list
        return (
            f"No namespaces returned. Could not determine whether Workload "
            f"Management is enabled on this vCenter ({type(exc).__name__}), so "
            f"this may be an empty Supervisor or no Supervisor at all."
        )
    if compat.get("wcp_query_failed"):
        return (
            "No namespaces returned, and the namespace-management query also "
            f"failed ({compat.get('wcp_query_error')}). Treat this zero as "
            "unknown, not as empty. Run `vmware-vks check`, then "
            "check_vks_compatibility."
        )
    if compat.get("workload_management_enabled") is False:
        return (
            "No namespaces returned because no cluster on this vCenter has "
            "Workload Management enabled — there is no Supervisor to hold "
            "namespaces. Enable it in vSphere Client > Workload Management, or "
            "target a vCenter that already has one."
        )
    return "No namespaces exist on this Supervisor yet."


def get_namespace(si: ServiceInstance, name: str) -> dict:
    """Get detailed info for a single namespace."""
    return _rest_get(si, f"/vcenter/namespaces/instances/{name}")


def create_namespace(
    si: ServiceInstance,
    name: str,
    cluster_id: str,
    storage_policy: str,
    cpu_limit: int | None = None,
    memory_limit_mib: int | None = None,
    description: str = "",
    dry_run: bool = False,
) -> dict:
    """Create a vSphere Namespace."""
    spec: dict = {
        "namespace": name,
        "cluster": cluster_id,
        "description": description,
        "storage_specs": [{"policy": storage_policy}],
    }
    resource_spec: dict = {}
    if cpu_limit is not None:
        resource_spec["cpu_limit"] = cpu_limit
    if memory_limit_mib is not None:
        resource_spec["memory_limit"] = memory_limit_mib
    if resource_spec:
        spec["resource_spec"] = resource_spec

    if dry_run:
        return {"dry_run": True, "spec": spec, "action": "create_namespace"}

    _rest_post(si, "/vcenter/namespaces/instances", spec)
    return {"namespace": name, "status": "created", "cluster": cluster_id}


def update_namespace(
    si: ServiceInstance,
    name: str,
    cpu_limit: int | None = None,
    memory_limit_mib: int | None = None,
    storage_policy: str | None = None,
) -> dict:
    """Update namespace resource quotas or storage policy."""
    spec: dict = {}
    resource_spec: dict = {}
    if cpu_limit is not None:
        resource_spec["cpu_limit"] = cpu_limit
    if memory_limit_mib is not None:
        resource_spec["memory_limit"] = memory_limit_mib
    if resource_spec:
        spec["resource_spec"] = resource_spec
    if storage_policy:
        spec["storage_specs"] = [{"policy": storage_policy}]
    if not spec:
        return {"namespace": name, "status": "no_changes"}
    _rest_patch(si, f"/vcenter/namespaces/instances/{name}", spec)
    return {"namespace": name, "status": "updated"}


def delete_namespace(
    si: ServiceInstance,
    name: str,
    confirmed: bool = False,
    dry_run: bool = False,
) -> dict:
    """Delete a vSphere Namespace.

    Guards: confirmed=True required; rejects if TKC clusters exist.
    dry_run is evaluated BEFORE confirmed — a preview never needs confirmation.
    """
    tkc_clusters = _list_tkc_in_namespace(si, name)
    if tkc_clusters:
        raise VksSafetyError(
            f"Cannot delete namespace '{name}': {len(tkc_clusters)} TKC "
            f"cluster(s) still exist inside it. Run delete_tkc_cluster for each "
            f"of those, then retry delete_namespace. Clusters: "
            f"{', '.join(tkc_clusters)}."
        )

    if dry_run:
        return {
            "dry_run": True,
            "action": "delete_namespace",
            "namespace": name,
            "warning": "This will permanently delete the namespace.",
        }

    if not confirmed:
        raise ValueError(
            f"confirmed=True required to delete namespace '{name}'. Re-run "
            f"delete_namespace with confirmed=True to proceed, or with "
            f"dry_run=True to preview what would be deleted."
        )

    _rest_delete(si, f"/vcenter/namespaces/instances/{name}")
    return {"namespace": name, "status": "deleted"}


def list_vm_classes(si: ServiceInstance) -> dict:
    """List available VM classes for TKC node sizing.

    VirtualMachineClasses.Info wire fields: memory is ``memory_MB`` and GPU
    info nests under ``devices`` (``vgpu_devices`` +
    ``dynamic_direct_path_io_devices``) — there is no flat gpu_count field,
    so we derive it from the device lists.

    Returns the family list envelope; the endpoint returns every class in one
    response, so ``total`` is the real count and nothing is truncated.
    """
    data = _rest_get(si, "/vcenter/namespace-management/virtual-machine-classes")
    classes = []
    for item in (data if isinstance(data, list) else []):
        devices = item.get("devices") or {}
        gpu_count = len(devices.get("vgpu_devices") or []) + len(
            devices.get("dynamic_direct_path_io_devices") or []
        )
        classes.append(
            {
                "id": item.get("id"),
                "cpu_count": item.get("cpu_count"),
                "memory_mb": item.get("memory_MB"),
                "gpu_count": gpu_count,
            }
        )
    return paginated(classes, total=len(classes))
