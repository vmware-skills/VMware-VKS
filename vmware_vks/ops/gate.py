"""The confirmation gate in front of the four VKS write tools (HLD §7, revised 2026-09-16).

The ops-layer executors (``namespace.create_namespace`` / ``delete_namespace``,
``tkc.create_tkc_cluster`` / ``delete_tkc_cluster``) stay what the CLI calls.
This module is what the MCP tools put in front of them:

* ``decide_*`` turns ``confirm`` plus the deprecated ``confirmed`` / ``dry_run``
  spellings into one decision — the conservative reading wins (L2);
* ``measure_*`` reads what the call would change or destroy (L1), with every
  field that could not be read listed in ``unmeasured`` instead of guessed;
* ``refuse_if_blocked`` raises a teaching error on a blocker or an unmeasured
  field, and is called only on the path that acts (L3).

Every read here is one the skill already makes for its own read tools.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from vmware_vks.errors import VksApiError, VksSafetyError, cause_summary
from vmware_vks.ops import namespace as _ns
from vmware_vks.ops import storage as _storage
from vmware_vks.ops import tkc as _tkc
from vmware_vks.ops import vmservice as _vms

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

#: Identifiers listed individually in a blast radius; the counts cover the rest.
MAX_LISTED = 16


class GateRefusedError(VksSafetyError):
    """``confirm=True`` was refused: a blocker, or a blast radius not measured.

    Carries the radius so the error envelope can show what was measured.
    """

    def __init__(self, message: str, blast_radius: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.blast_radius = blast_radius


# ---------------------------------------------------------------------------
# confirm
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    """Whether the call acts."""

    act: bool


def decide_delete(confirm: bool) -> Decision:
    """Only ``confirm=True`` itself acts.

    The deprecated ``confirmed`` / ``dry_run`` aliases were removed in 3.4.2; a
    call still passing one is refused by the tool's argument model.
    """
    return Decision(act=confirm is True)


def decide_create(confirm: bool) -> Decision:
    """Only ``confirm=True`` itself acts (see :func:`decide_delete`)."""
    return Decision(act=confirm is True)


# ---------------------------------------------------------------------------
# L3
# ---------------------------------------------------------------------------


def refuse_if_blocked(tool: str, subject: str, radius: dict[str, Any]) -> None:
    """Raise before any write when the blast radius has a blocker or a gap.

    The remedy comes first: the MCP layer caps the message at 300 characters.
    """
    if radius["blockers"]:
        raise GateRefusedError(
            f"{tool} refused for '{subject}': {radius['blockers'][0]}", radius
        )
    if radius["unmeasured"]:
        escape = (
            ", or re-run with force=True to skip the workload check"
            if "workloads" in radius["unmeasured"] else ""
        )
        raise GateRefusedError(
            f"{tool} refused for '{subject}': could not read "
            f"{', '.join(radius['unmeasured'])}, so what it changes is unknown. Run "
            f"`vmware-vks check`, then preview again{escape}. Nothing was changed.",
            radius,
        )


def _try(read: Callable[[], Any]) -> tuple[Any, str | None]:
    """``(value, None)``, or ``(None, cause)`` when the read failed."""
    try:
        return read(), None
    except Exception as exc:  # noqa: BLE001 — any failed read is "unmeasured", never zero
        return None, cause_summary(exc)


# ---------------------------------------------------------------------------
# delete_namespace
# ---------------------------------------------------------------------------


def measure_namespace_delete(si: ServiceInstance, name: str) -> dict[str, Any]:
    """What deleting namespace ``name`` destroys: its TKC clusters, VMs and PVCs.

    The namespace itself must be readable — a name that does not exist raises
    the REST layer's teaching 404 rather than previewing nothing.
    """
    info = _ns.get_namespace(si, name) or {}
    tkc, tkc_err = _try(lambda: [c["name"] for c in _tkc.list_tkc_clusters(si, namespace=name)["items"]])
    vms, vm_err = _try(lambda: _vms.list_vm_names(si, name))
    pvcs, pvc_err = _try(lambda: _storage.list_namespace_storage_usage(si, name)["items"])

    unmeasured = [
        field for field, err in (("tkc_clusters", tkc_err), ("vms", vm_err), ("pvcs", pvc_err))
        if err is not None
    ]
    blockers = []
    if tkc:
        blockers.append(
            f"{len(tkc)} TKC cluster(s) still exist inside it. Run delete_tkc_cluster "
            f"for each, then preview delete_namespace again. Clusters: {', '.join(tkc[:5])}."
        )
    vm_names = vms["items"] if vms is not None else None
    return {
        "namespace": name,
        "config_status": info.get("config_status"),
        "tkc_cluster_count": len(tkc) if tkc is not None else None,
        "tkc_clusters": (tkc or [])[:MAX_LISTED],
        "vm_count": len(vm_names) if vm_names is not None else None,
        "vms": (vm_names or [])[:MAX_LISTED],
        "vm_service": vms.get("vm_service") if vms is not None else None,
        "pvc_count": len(pvcs) if pvcs is not None else None,
        "pvcs": [
            {"name": p.get("name"), "capacity": p.get("capacity")} for p in (pvcs or [])
        ][:MAX_LISTED],
        "blockers": blockers,
        "unmeasured": unmeasured,
        "read_errors": {
            k: v for k, v in (("tkc_clusters", tkc_err), ("vms", vm_err), ("pvcs", pvc_err)) if v
        },
    }


# ---------------------------------------------------------------------------
# delete_tkc_cluster
# ---------------------------------------------------------------------------


def _node_counts(topology: dict) -> tuple[int | None, int | None, dict | None]:
    """Control-plane replicas, total workers, and the workers' range if autoscaled.

    A pool without ``replicas`` is sized by the cluster-autoscaler: its count
    is unknown (``None``, never zero), and the range comes from its min/max
    annotations when every such pool declares both.
    """
    cp = topology.get("control_plane_replicas")
    pools = topology.get("worker_pools") or []
    fixed = [p["replicas"] for p in pools if isinstance(p.get("replicas"), int)]
    auto = [p for p in pools if not isinstance(p.get("replicas"), int)]
    workers = None if auto else sum(fixed)
    span = None
    if auto and all(isinstance(p.get("min_size"), int) and isinstance(p.get("max_size"), int)
                    for p in auto):
        span = {"min": sum(fixed) + sum(p["min_size"] for p in auto),
                "max": sum(fixed) + sum(p["max_size"] for p in auto)}
    return (cp if isinstance(cp, int) else None), workers, span


def _read_topology(si: ServiceInstance, name: str, namespace: str) -> tuple[Any, str | None]:
    """The cluster's topology; a 404 is the teaching error, not "could not read"."""
    try:
        return _tkc.read_tkc_topology(si, name, namespace), None
    except VksApiError as exc:
        if exc.status_code == 404:
            raise
        return None, cause_summary(exc)
    except Exception as exc:  # noqa: BLE001 — any other failed read is "unmeasured"
        return None, cause_summary(exc)


def measure_tkc_delete(
    si: ServiceInstance, name: str, namespace: str, force: bool
) -> dict[str, Any]:
    """What deleting TKC ``name`` destroys: its nodes, and the workloads on them.

    ``force=True`` keeps its old meaning — the running-workload check is not
    run at all (it needs the cluster's own API, which is often what is broken
    when force is wanted) — and the radius says so under ``skipped``.
    """
    topology, topo_err = _read_topology(si, name, namespace)
    cp, workers, span = (
        _node_counts(topology) if topology is not None else (None, None, None)
    )

    workloads, wl_err, skipped = None, None, []
    if force:
        skipped.append("workloads: force=True skips the running-workload check")
    else:
        workloads, wl_err = _try(lambda: _tkc._check_running_workloads(si, name, namespace))

    unmeasured = []
    # Deleting the cluster removes all its nodes whatever their number, so an
    # autoscaled pool's unknown count is information, not a gap to refuse on.
    if topo_err is not None or cp is None:
        unmeasured.append("node_counts")
    if wl_err is not None:
        unmeasured.append("workloads")
    blockers = []
    if workloads:
        listed = ", ".join(f"{w['kind']}/{w['name']}" for w in workloads[:5])
        blockers.append(
            f"{len(workloads)} running workload(s). Drain them (issue_tkc_kubeconfig), "
            f"or re-run delete_tkc_cluster with force=True to delete anyway. "
            f"Running: {listed}."
        )
    return {
        "cluster": name,
        "namespace": namespace,
        "phase": (topology or {}).get("phase"),
        "k8s_version": (topology or {}).get("k8s_version"),
        "control_plane_nodes": cp,
        "worker_nodes": workers,
        "worker_nodes_range": span,
        "node_count": cp + workers if cp is not None and workers is not None else None,
        "worker_pools": (topology or {}).get("worker_pools", [])[:MAX_LISTED],
        "workload_count": len(workloads) if workloads is not None else None,
        "workloads": workloads[:MAX_LISTED] if workloads is not None else None,
        "force": force,
        "blockers": blockers,
        "unmeasured": unmeasured,
        "skipped": skipped,
        "read_errors": {k: v for k, v in (("node_counts", topo_err), ("workloads", wl_err)) if v},
    }


# ---------------------------------------------------------------------------
# create_namespace
# ---------------------------------------------------------------------------


def measure_namespace_create(
    si: ServiceInstance, name: str, spec: dict[str, Any]
) -> dict[str, Any]:
    """What creating namespace ``name`` adds, and whether the name is free."""
    listed, err = _try(lambda: _ns._rest_get(si, "/vcenter/namespaces/instances"))
    if err is None and not isinstance(listed, list):
        # Not a list is not "no namespaces": what exists is unknown.
        err = f"unexpected response shape ({type(listed).__name__})"
    names = [i.get("namespace") for i in listed] if err is None else None
    taken = name in names if names is not None else None
    blockers = []
    if taken:
        blockers.append(
            "a namespace with this name already exists. Use update_namespace to "
            "change its quotas or storage policy, or pick another name."
        )
    return {
        "namespace": name,
        "cluster_id": spec.get("cluster"),
        "storage_policy": (spec.get("storage_specs") or [{}])[0].get("policy"),
        "spec": spec,
        "name_taken": taken,
        "blockers": blockers,
        "unmeasured": [] if err is None else ["existing_namespaces"],
        "read_errors": {"existing_namespaces": err} if err else {},
    }


# ---------------------------------------------------------------------------
# create_tkc_cluster
# ---------------------------------------------------------------------------


def measure_tkc_create(
    si: ServiceInstance,
    name: str,
    namespace: str,
    k8s_version: str,
    vm_class: str,
    control_plane_count: int,
    worker_count: int,
    storage_class: str,
) -> dict[str, Any]:
    """The manifest that would be applied, and whether the name is free."""
    api_version = _tkc._resolve_tkc_version(si, namespace)
    manifest = _tkc.generate_tkc_yaml(
        name=name, namespace=namespace, k8s_version=k8s_version, vm_class=vm_class,
        control_plane_count=control_plane_count, worker_count=worker_count,
        storage_class=storage_class, api_version=api_version,
    )
    existing, err = _try(
        lambda: [c["name"] for c in _tkc.list_tkc_clusters(si, namespace=namespace)["items"]]
    )
    blockers = []
    if existing is not None and name in existing:
        blockers.append(
            f"a TKC cluster named '{name}' already exists in namespace '{namespace}'. "
            "Use scale_tkc_cluster or upgrade_tkc_cluster to change it, or pick another name."
        )
    return {
        "cluster": name,
        "namespace": namespace,
        "k8s_version": k8s_version,
        "vm_class": vm_class,
        "storage_class": storage_class,
        "control_plane_nodes": control_plane_count,
        "worker_nodes": worker_count,
        "node_count": control_plane_count + worker_count,
        "api_version": api_version,
        "yaml": manifest,
        "existing_cluster_count": len(existing) if existing is not None else None,
        "blockers": blockers,
        "unmeasured": [] if err is None else ["existing_clusters"],
        "read_errors": {"existing_clusters": err} if err else {},
    }
