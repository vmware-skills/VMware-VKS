"""VM Service (vm-operator) read-only operations.

These read Supervisor Kubernetes CRDs in the ``vmoperator.vmware.com`` API
group via the kubernetes client (CustomObjectsApi) — the same Layer-2 path as
the TKC ops, NOT vCenter REST and NOT pyVmomi. Covered kinds:

* ``VirtualMachineSnapshot``  (plural ``virtualmachinesnapshots``, NEW at
  v1alpha5) — VM snapshots in a namespace.
* ``VirtualMachine``          (plural ``virtualmachines``) — multi-NIC readout
  from ``spec.network.interfaces[]``.
* ``VirtualMachineGroup``     (plural ``virtualmachinegroups``, v1alpha4+) —
  VM groups and their ``spec.bootOrder``.

The served CRD version varies by Supervisor build, so it is DISCOVERED at
runtime through the K8s discovery API (``ApisApi.get_api_versions()``), newest
served among v1alpha4/5/6 preferred — never hardcoded. Every group/plural this
module asks for is pinned in ``tests/eval/spec/vmservice_endpoints.py`` and a
regression test asserts this module touches nothing else (anti-phantom-endpoint
guard, CLAUDE.md 踩坑 #36).

All functions are read-only and take ``(si: ServiceInstance)`` first.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

from vmware_policy import paginated, sanitize

from vmware_vks.errors import VksApiError

_log = logging.getLogger("vmware-vks.ops.vmservice")

# --- Pinned surface (kept in lock-step with tests/eval/spec/vmservice_endpoints).
# The single API group for every VM Service CRD. Anything else is a phantom.
_VMOP_GROUP = "vmoperator.vmware.com"

# Served versions vmoperator may ship, NEWEST-FIRST. The exact served version
# is resolved at runtime; this only bounds and orders the candidate set.
_VMOP_VERSIONS: tuple[str, ...] = (
    "v1alpha6",
    "v1alpha5",
    "v1alpha4",
    "v1alpha3",
    "v1alpha2",
    "v1alpha1",
)

_PLURAL_VM = "virtualmachines"
_PLURAL_SNAPSHOT = "virtualmachinesnapshots"
_PLURAL_GROUP = "virtualmachinegroups"

# plural -> (kind, minimum served version the kind first appears at)
_RESOURCES: dict[str, tuple[str, str]] = {
    _PLURAL_VM: ("VirtualMachine", "v1alpha1"),
    _PLURAL_SNAPSHOT: ("VirtualMachineSnapshot", "v1alpha5"),
    _PLURAL_GROUP: ("VirtualMachineGroup", "v1alpha4"),
}

# All-namespace/collection page size — walked with the continue token so a
# large collection arrives in bounded chunks instead of one response.
_LIST_PAGE_LIMIT = 500

# Per-vCenter-host cache of the discovered served versions for the vmoperator
# group. Keyed the same way as tkc._version_cache so both stay aligned.
_served_cache: dict[str, frozenset[str]] = {}


def _si_host(si: ServiceInstance) -> str:
    """vCenter host key for the served-version cache."""
    return getattr(getattr(si, "_stub", None), "host", "default")


def invalidate_served_versions(si: ServiceInstance) -> None:
    """Drop the cached served-version set for this connection's host."""
    _served_cache.pop(_si_host(si), None)


def _get_custom_objects_api(si: ServiceInstance, namespace: str):
    """Get a kubernetes CustomObjectsApi bound to the Supervisor namespace."""
    import kubernetes as k8s

    from vmware_vks.k8s_connection import get_k8s_client

    api_client = get_k8s_client(si, namespace)
    return k8s.client.CustomObjectsApi(api_client)


def _translate(si: ServiceInstance, exc: Exception, resource: str, namespace: str, kind: str):
    """Wrap a kubernetes ApiException into a teaching VksApiError (踩坑 #37)."""
    from vmware_vks.k8s_connection import translate_k8s_error

    return translate_k8s_error(si, exc, resource=resource, namespace=namespace, kind=kind)


def _discover_served_versions(si: ServiceInstance, namespace: str) -> frozenset[str]:
    """Discover (and cache per host) the served versions of the vmoperator group.

    Uses the K8s discovery API rather than hardcoding a version. A successful
    call where the group is simply absent caches (and returns) an empty set —
    that is a real answer ("VM Service CRDs are not installed"), distinct from a
    transport/auth failure, which is translated into a teaching error instead.
    """
    host = _si_host(si)
    cached = _served_cache.get(host)
    if cached is not None:
        return cached

    import kubernetes as k8s

    api_client = _get_custom_objects_api(si, namespace).api_client
    try:
        apis = k8s.client.ApisApi(api_client)
        try:
            groups = apis.get_api_versions().groups
        except k8s.client.exceptions.ApiException as e:
            raise _translate(si, e, "(discovery)", namespace, "vmoperator") from e
        served: frozenset[str] = frozenset()
        for g in groups or []:
            if getattr(g, "name", None) == _VMOP_GROUP:
                served = frozenset(
                    getattr(v, "version", "") for v in (getattr(g, "versions", None) or [])
                )
                break
    finally:
        api_client.close()

    _served_cache[host] = served
    return served


def _pick_version(served: frozenset[str], min_version: str) -> str | None:
    """Newest served version that is at least ``min_version``, or ``None``.

    Walks ``_VMOP_VERSIONS`` newest-first from the top down to ``min_version``
    inclusive, so v1alpha6 is preferred over v1alpha5 over v1alpha4.
    """
    min_idx = _VMOP_VERSIONS.index(min_version)
    for candidate in _VMOP_VERSIONS[: min_idx + 1]:
        if candidate in served:
            return candidate
    return None


def _resolve_version(si: ServiceInstance, namespace: str, plural: str) -> str:
    """Resolve the served version to use for ``plural`` on this Supervisor.

    Raises a teaching :class:`VksApiError` when no served version satisfies the
    kind's minimum — e.g. VirtualMachineSnapshot needs v1alpha5+ and an older
    Supervisor simply does not carry the CRD (do not invent a version).
    """
    kind, min_version = _RESOURCES[plural]
    served = _discover_served_versions(si, namespace)
    picked = _pick_version(served, min_version)
    if picked is None:
        found = ", ".join(sorted(served)) or "none"
        raise VksApiError(
            f"The {kind} CRD (plural '{plural}') is not served on this Supervisor: "
            f"it requires {_VMOP_GROUP}/{min_version} or newer, but the served "
            f"versions are: {found}. VirtualMachineSnapshot needs a Supervisor at "
            f"vmoperator v1alpha5+ and VirtualMachineGroup needs v1alpha4+ — "
            f"upgrade the Supervisor, or run "
            f"'kubectl api-resources | grep vmoperator' against it to confirm what "
            f"is served."
        )
    return picked


def _list_all(list_call: Callable[..., dict]) -> list[dict]:
    """Collect every item from a paginated custom-object list call.

    ``list_call(limit, _continue)`` returns the raw dict response
    (``{"items": [...], "metadata": {"continue": ...}}``). Walks the continue
    token so a large collection is fetched to exhaustion in bounded chunks.
    """
    items: list[dict] = []
    cont: str | None = None
    while True:
        raw = list_call(limit=_LIST_PAGE_LIMIT, _continue=cont) or {}
        items.extend(raw.get("items", []) or [])
        cont = (raw.get("metadata") or {}).get("continue")
        if not cont:
            break
    return items


def _s(value: Any, max_len: int = 500) -> str:
    """Sanitize any value to a safe string, treating None/absent as empty.

    Responses are unverified: a missing field must degrade to "" and never
    crash (踩坑 形态 #1). Non-string values are coerced before sanitizing.
    """
    if value is None:
        return ""
    return sanitize(str(value), max_len)


def _condition_status(status: dict, cond_type: str) -> str | None:
    """Status of the named condition (e.g. 'Ready'), or None if not present."""
    for cond in status.get("conditions", []) or []:
        if isinstance(cond, dict) and cond.get("type") == cond_type:
            return cond.get("status")
    return None


def _snapshot_row(item: dict) -> dict:
    meta = item.get("metadata", {}) or {}
    spec = item.get("spec", {}) or {}
    status = item.get("status", {}) or {}
    # vm-operator has used vmName across versions; guard for either spelling.
    vm_name = spec.get("vmName") or spec.get("virtualMachineName") or ""
    return {
        "name": _s(meta.get("name")),
        "namespace": _s(meta.get("namespace")),
        "vm_name": _s(vm_name),
        "created": meta.get("creationTimestamp"),
        "ready": _condition_status(status, "Ready"),
    }


def _group_row(item: dict) -> dict:
    meta = item.get("metadata", {}) or {}
    spec = item.get("spec", {}) or {}
    boot_order = []
    for stage in spec.get("bootOrder", []) or []:
        if not isinstance(stage, dict):
            continue
        members = [
            {"kind": _s(m.get("kind")), "name": _s(m.get("name"))}
            for m in (stage.get("members", []) or [])
            if isinstance(m, dict)
        ]
        entry: dict[str, Any] = {"members": members}
        if stage.get("powerOnDelay") is not None:
            entry["power_on_delay"] = _s(stage.get("powerOnDelay"), 64)
        boot_order.append(entry)
    return {
        "name": _s(meta.get("name")),
        "namespace": _s(meta.get("namespace")),
        "boot_order": boot_order,
        "member_count": sum(len(stage["members"]) for stage in boot_order),
    }


def _interface_row(nic: dict, index: int) -> dict:
    if not isinstance(nic, dict):
        return {"name": f"eth{index}", "network_name": "", "network_kind": "", "network_api_version": ""}
    net = nic.get("network", {}) or {}
    return {
        "name": _s(nic.get("name") or f"eth{index}", 128),
        "network_name": _s(net.get("name"), 128),
        "network_kind": _s(net.get("kind"), 128),
        "network_api_version": _s(net.get("apiVersion"), 128),
    }


def list_vm_snapshots(si: ServiceInstance, namespace: str) -> dict:
    """List VirtualMachineSnapshot objects in a namespace (read-only).

    Returns the family list envelope; items are
    {name, namespace, vm_name, created, ready}. The collection is walked to
    exhaustion so ``total`` is the real count and ``truncated`` is False. The
    served version is discovered at runtime and echoed as ``served_version``.
    """
    import kubernetes as k8s

    version = _resolve_version(si, namespace, _PLURAL_SNAPSHOT)
    api = _get_custom_objects_api(si, namespace)
    try:
        try:
            items = _list_all(
                lambda **kw: api.list_namespaced_custom_object(
                    group=_VMOP_GROUP, version=version, namespace=namespace,
                    plural=_PLURAL_SNAPSHOT, **kw,
                )
            )
        except k8s.client.exceptions.ApiException as e:
            raise _translate(si, e, "(list)", namespace, "VirtualMachineSnapshot") from e
        rows = [_snapshot_row(it) for it in items]
        return paginated(
            rows, total=len(rows), namespace=namespace, served_version=version
        )
    finally:
        api.api_client.close()


def list_vm_groups(si: ServiceInstance, namespace: str) -> dict:
    """List VirtualMachineGroup objects and their bootOrder in a namespace (read-only).

    Returns the family list envelope; items are
    {name, namespace, boot_order, member_count}, where ``boot_order`` mirrors
    ``spec.bootOrder`` as a list of {members: [{kind, name}], power_on_delay?}.
    Walked to exhaustion, so ``total`` is real and ``truncated`` is False.
    """
    import kubernetes as k8s

    version = _resolve_version(si, namespace, _PLURAL_GROUP)
    api = _get_custom_objects_api(si, namespace)
    try:
        try:
            items = _list_all(
                lambda **kw: api.list_namespaced_custom_object(
                    group=_VMOP_GROUP, version=version, namespace=namespace,
                    plural=_PLURAL_GROUP, **kw,
                )
            )
        except k8s.client.exceptions.ApiException as e:
            raise _translate(si, e, "(list)", namespace, "VirtualMachineGroup") from e
        rows = [_group_row(it) for it in items]
        return paginated(
            rows, total=len(rows), namespace=namespace, served_version=version
        )
    finally:
        api.api_client.close()


def list_vm_network_interfaces(
    si: ServiceInstance, namespace: str, vm_name: str
) -> dict:
    """List the network interfaces of one VirtualMachine (multi-NIC readout, read-only).

    Reads ``spec.network.interfaces[]`` off a single VirtualMachine. Returns the
    family list envelope; items are
    {name, network_name, network_kind, network_api_version}. A VM with no
    ``spec.network`` degrades to an empty list, never an error.
    """
    import kubernetes as k8s

    version = _resolve_version(si, namespace, _PLURAL_VM)
    api = _get_custom_objects_api(si, namespace)
    try:
        try:
            vm = api.get_namespaced_custom_object(
                group=_VMOP_GROUP, version=version, namespace=namespace,
                plural=_PLURAL_VM, name=vm_name,
            )
        except k8s.client.exceptions.ApiException as e:
            raise _translate(si, e, vm_name, namespace, "VirtualMachine") from e
        network = (vm.get("spec", {}) or {}).get("network", {}) or {}
        interfaces = network.get("interfaces", []) or []
        rows = [_interface_row(nic, i) for i, nic in enumerate(interfaces)]
        return paginated(
            rows, total=len(rows), namespace=namespace,
            vm_name=_s(vm_name, 253), served_version=version,
        )
    finally:
        api.api_client.close()
