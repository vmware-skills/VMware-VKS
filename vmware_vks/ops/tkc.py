"""TanzuKubernetesCluster (TKC) lifecycle operations.

Uses cluster.x-k8s.io API (vSphere 8.x). The Supervisor's served version
is detected at runtime — vSphere 8.0 ships v1beta1, later releases may
expose v1 alongside it. All cluster operations go through the Supervisor
K8s API endpoint (Layer 2).

Safety:
- delete_tkc_cluster rejects if Deployments/StatefulSets/DaemonSets are running
- create_tkc_cluster defaults to dry_run=True (returns YAML plan)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

from vmware_policy import paginated, sanitize

from vmware_vks.errors import VksApiError, VksSafetyError, cause_summary

_log = logging.getLogger("vmware-vks.ops.tkc")


def _translate_api_exception(si, exc, resource: str, namespace: str):
    """Wrap kubernetes ApiException into a teaching VksApiError (踩坑 #37)."""
    from vmware_vks.k8s_connection import translate_k8s_error

    return translate_k8s_error(si, exc, resource=resource, namespace=namespace)

_TKC_GROUP = "cluster.x-k8s.io"
_TKC_VERSION_FALLBACK = "v1beta1"
_TKC_VERSION_PREFERENCE = ("v1", "v1beta1")  # probe order: prefer v1 if served
_TKC_PLURAL = "clusters"

# Per-host cache: vCenter host → resolved API version. Keeps repeated calls
# from re-probing the discovery API on every TKC operation.
_version_cache: dict[str, str] = {}

# Page size for all-namespace list calls. Chunks huge fleets over multiple
# round-trips (limit/continue) instead of landing everything in one response.
_LIST_PAGE_LIMIT = 500


def _get_custom_objects_api(si: ServiceInstance, namespace: str):
    """Get kubernetes CustomObjectsApi connected to Supervisor namespace."""
    import kubernetes as k8s
    from vmware_vks.k8s_connection import get_k8s_client
    api_client = get_k8s_client(si, namespace)
    return k8s.client.CustomObjectsApi(api_client)


def _list_all_custom(list_call) -> list:
    """Collect all items from a paginated custom-object list call.

    ``list_call(limit, _continue)`` returns the raw dict response
    (``{"items": [...], "metadata": {"continue": ...}}``). Walks the
    continue token so a very large collection is fetched in bounded chunks.
    """
    items: list = []
    cont: str | None = None
    while True:
        raw = list_call(limit=_LIST_PAGE_LIMIT, _continue=cont)
        items.extend(raw.get("items", []))
        cont = (raw.get("metadata") or {}).get("continue")
        if not cont:
            break
    return items


def _list_all_typed(list_call) -> list:
    """Collect all ``.items`` from a paginated typed kubernetes list call.

    ``list_call(limit, _continue)`` returns a typed list object (e.g.
    ``V1DeploymentList``) with ``.items`` and ``.metadata._continue``.
    """
    items: list = []
    cont: str | None = None
    while True:
        page = list_call(limit=_LIST_PAGE_LIMIT, _continue=cont)
        items.extend(page.items)
        cont = getattr(page.metadata, "_continue", None) if page.metadata else None
        if not cont:
            break
    return items


def _resolve_tkc_version(si: ServiceInstance, namespace: str) -> str:
    """Probe the Supervisor's served cluster.x-k8s.io versions and pick one.

    Prefers v1 when available (vSphere releases that ship Cluster API v1),
    falls back to v1beta1 (vSphere 8.0). Result is cached per vCenter host.
    """
    host = getattr(getattr(si, "_stub", None), "host", "default")
    cached = _version_cache.get(host)
    if cached:
        return cached

    import kubernetes as k8s
    from vmware_vks.k8s_connection import get_k8s_client

    api_client = get_k8s_client(si, namespace)
    try:
        apis = k8s.client.ApisApi(api_client)
        groups = apis.get_api_versions().groups
        served = set()
        for g in groups:
            if g.name == _TKC_GROUP:
                served = {v.version for v in g.versions}
                break
        for preferred in _TKC_VERSION_PREFERENCE:
            if preferred in served:
                _version_cache[host] = preferred
                return preferred
    except Exception as e:
        _log.warning(
            "TKC API discovery failed on %s: %s — falling back to %s",
            host, e, _TKC_VERSION_FALLBACK,
        )
    finally:
        api_client.close()

    _version_cache[host] = _TKC_VERSION_FALLBACK
    return _TKC_VERSION_FALLBACK


def generate_tkc_yaml(
    name: str,
    namespace: str,
    k8s_version: str,
    vm_class: str,
    control_plane_count: int,
    worker_count: int,
    storage_class: str,
    api_version: str = _TKC_VERSION_FALLBACK,
) -> str:
    """Generate TKC cluster YAML.

    api_version defaults to v1beta1 (vSphere 8.0). Pass "v1" when targeting
    Supervisors that have promoted Cluster API to v1.
    """
    if worker_count < 1:
        raise ValueError(
            f"worker_count must be >= 1, got {worker_count}. Pass worker_count=1 "
            f"or more to create_tkc_cluster."
        )
    if control_plane_count not in (1, 3):
        raise ValueError(
            f"control_plane_count must be 1 or 3, got {control_plane_count}. Pass "
            f"1 for a single control plane node or 3 for HA to create_tkc_cluster."
        )

    manifest = {
        "apiVersion": f"{_TKC_GROUP}/{api_version}",
        "kind": "Cluster",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "clusterNetwork": {
                "pods": {"cidrBlocks": ["192.168.0.0/16"]},
                "services": {"cidrBlocks": ["10.96.0.0/12"]},
            },
            "topology": {
                "class": "tanzukubernetescluster",
                "version": k8s_version,
                "controlPlane": {
                    "replicas": control_plane_count,
                    "metadata": {},
                    "nodeDrainTimeout": "60s",
                },
                "workers": {
                    "machineDeployments": [{
                        "class": "node-pool",
                        "name": "worker-pool",
                        "replicas": worker_count,
                        "metadata": {},
                        "nodeDrainTimeout": "60s",
                    }]
                },
                "variables": [
                    {"name": "vmClass", "value": vm_class},
                    {"name": "storageClass", "value": storage_class},
                ],
            },
        },
    }
    return yaml.dump(manifest, default_flow_style=False)


def list_tkc_clusters(si: ServiceInstance, namespace: str | None = None) -> dict:
    """List TKC clusters in a namespace (or all namespaces).

    Returns the family list envelope. Paging walks the continue token to
    exhaustion, so ``total`` is the real cluster count and ``truncated`` is
    always False — the agent is told the listing is complete.

    .. deprecated:: 1.8.6
       ``clusters`` is a compatibility alias for ``items`` and will be removed
       in 2.0. Until v1.8.0 this function returned ``{total, clusters}``; the
       envelope renamed ``clusters`` to ``items``, and because the payload was
       already a keyed dict the break was silent — ``result.get("clusters", [])``
       started returning ``[]``, which reads as "this namespace has no
       clusters" rather than as a failure. Both keys are the *same* list
       object, so they cannot drift. Migrate to ``items``.
    """
    import kubernetes as k8s

    ns = namespace or "default"
    version = _resolve_tkc_version(si, ns)
    api = _get_custom_objects_api(si, ns)
    try:
        try:
            if namespace:
                items = _list_all_custom(
                    lambda **kw: api.list_namespaced_custom_object(
                        group=_TKC_GROUP, version=version,
                        namespace=namespace, plural=_TKC_PLURAL, **kw,
                    )
                )
            else:
                items = _list_all_custom(
                    lambda **kw: api.list_cluster_custom_object(
                        group=_TKC_GROUP, version=version, plural=_TKC_PLURAL, **kw,
                    )
                )
        except k8s.client.exceptions.ApiException as e:
            raise _translate_api_exception(si, e, resource="(list)", namespace=ns) from e
        clusters = [
            {
                "name": sanitize(item["metadata"]["name"]),
                "namespace": sanitize(item["metadata"]["namespace"]),
                "phase": item.get("status", {}).get("phase", "Unknown"),
                "k8s_version": item.get("spec", {}).get("topology", {}).get("version", ""),
            }
            for item in items
        ]
        envelope = paginated(clusters, total=len(clusters))
        # Deprecated alias for pre-v1.8.0 callers; removed in 2.0. Same list
        # object as ``items`` — a copy would let the two drift.
        return {**envelope, "clusters": envelope["items"]}
    finally:
        api.api_client.close()


def get_tkc_cluster(si: ServiceInstance, name: str, namespace: str) -> dict:
    """Get detailed TKC cluster info."""
    import kubernetes as k8s

    version = _resolve_tkc_version(si, namespace)
    api = _get_custom_objects_api(si, namespace)
    try:
        try:
            raw = api.get_namespaced_custom_object(
                group=_TKC_GROUP, version=version,
                namespace=namespace, plural=_TKC_PLURAL, name=name,
            )
        except k8s.client.exceptions.ApiException as e:
            raise _translate_api_exception(si, e, resource=name, namespace=namespace) from e
        status = raw.get("status", {})
        conditions = [
            {
                "type": sanitize(c.get("type", "")),
                "status": c.get("status", ""),
                "message": sanitize(c.get("message", ""), max_len=500),
            }
            for c in status.get("conditions", [])
        ]
        # Guard nested lookups — a half-provisioned cluster may miss any level.
        topology = raw.get("spec", {}).get("topology", {})
        machine_deployments = (
            topology.get("workers", {}).get("machineDeployments") or []
        )
        worker_replicas = (
            machine_deployments[0].get("replicas") if machine_deployments else None
        )
        return {
            "name": name,
            "namespace": namespace,
            "phase": status.get("phase"),
            "k8s_version": topology.get("version"),
            "control_plane_replicas": topology.get("controlPlane", {}).get("replicas"),
            "worker_replicas": worker_replicas,
            "conditions": conditions,
            "infrastructure_ready": status.get("infrastructureReady", False),
            "control_plane_ready": status.get("controlPlaneReady", False),
        }
    finally:
        api.api_client.close()


_AUTOSCALER_MIN = "cluster.x-k8s.io/cluster-api-autoscaler-node-group-min-size"
_AUTOSCALER_MAX = "cluster.x-k8s.io/cluster-api-autoscaler-node-group-max-size"


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _worker_pool(pool: dict) -> dict:
    """One ClusterClass worker pool: its replicas, or its autoscaler range."""
    entry = {"name": sanitize(str(pool.get("name", ""))), "replicas": pool.get("replicas")}
    if isinstance(entry["replicas"], int):
        return entry
    annotations = (pool.get("metadata") or {}).get("annotations") or {}
    return {
        **entry,
        "replicas": None,
        "autoscaled": True,
        "min_size": _int_or_none(annotations.get(_AUTOSCALER_MIN)),
        "max_size": _int_or_none(annotations.get(_AUTOSCALER_MAX)),
    }


def read_tkc_topology(si: ServiceInstance, name: str, namespace: str) -> dict:
    """The declared node topology of one cluster, every worker pool included.

    Same read as :func:`get_tkc_cluster`, which reports only the first pool's
    replicas; the delete blast radius needs all of them. A pool without a
    ``replicas`` value counts as ``None`` (unknown), never as zero; it is the
    normal shape of a pool the cluster-autoscaler sizes, so such a pool is
    marked ``autoscaled`` with the min/max its annotations declare (each
    ``None`` when absent or not an integer).
    """
    import kubernetes as k8s

    version = _resolve_tkc_version(si, namespace)
    api = _get_custom_objects_api(si, namespace)
    try:
        try:
            raw = api.get_namespaced_custom_object(
                group=_TKC_GROUP, version=version,
                namespace=namespace, plural=_TKC_PLURAL, name=name,
            )
        except k8s.client.exceptions.ApiException as e:
            raise _translate_api_exception(si, e, resource=name, namespace=namespace) from e
        topology = (raw.get("spec") or {}).get("topology") or {}
        pools = (topology.get("workers") or {}).get("machineDeployments") or []
        return {
            "phase": (raw.get("status") or {}).get("phase"),
            "k8s_version": topology.get("version"),
            "control_plane_replicas": (topology.get("controlPlane") or {}).get("replicas"),
            "worker_pools": [_worker_pool(p) for p in pools],
        }
    finally:
        api.api_client.close()


def get_tkc_available_versions(si: ServiceInstance, namespace: str) -> dict:
    """List K8s versions available for TKC clusters on this Supervisor."""
    import kubernetes as k8s
    from vmware_vks.k8s_connection import get_k8s_client

    api_client = get_k8s_client(si, namespace)
    try:
        custom_api = k8s.client.CustomObjectsApi(api_client)
        raw = custom_api.list_cluster_custom_object(
            group="run.tanzu.vmware.com",
            version="v1alpha3",
            plural="tanzukubernetesreleases",
        )
        versions = [
            {
                "name": item["metadata"]["name"],
                "version": item["spec"].get("version", item["metadata"]["name"]),
            }
            for item in raw.get("items", [])
        ]
        return {"versions": sorted(versions, key=lambda x: x["version"], reverse=True)}
    except Exception as e:
        return {"versions": [], "error": str(e),
                "hint": "TanzuKubernetesRelease API may not be available on this Supervisor"}
    finally:
        api_client.close()


def create_tkc_cluster(
    si: ServiceInstance,
    name: str,
    namespace: str,
    k8s_version: str,
    vm_class: str,
    control_plane_count: int = 1,
    worker_count: int = 3,
    storage_class: str = "vsphere-storage",
    dry_run: bool = True,
) -> dict:
    """Create a TKC cluster. dry_run=True returns YAML plan without applying."""
    version = _resolve_tkc_version(si, namespace) if not dry_run else _TKC_VERSION_FALLBACK
    yaml_str = generate_tkc_yaml(
        name=name, namespace=namespace, k8s_version=k8s_version,
        vm_class=vm_class, control_plane_count=control_plane_count,
        worker_count=worker_count, storage_class=storage_class,
        api_version=version,
    )

    if dry_run:
        return {
            "dry_run": True,
            "action": "create_tkc_cluster",
            "name": name,
            "namespace": namespace,
            "yaml": yaml_str,
            "hint": "Set dry_run=False to apply this manifest.",
        }

    import kubernetes as k8s

    manifest = yaml.safe_load(yaml_str)
    api = _get_custom_objects_api(si, namespace)
    try:
        try:
            api.create_namespaced_custom_object(
                group=_TKC_GROUP, version=version,
                namespace=namespace, plural=_TKC_PLURAL, body=manifest,
            )
        except k8s.client.exceptions.ApiException as e:
            raise _translate_api_exception(si, e, resource=name, namespace=namespace) from e
        return {"name": name, "namespace": namespace, "status": "creating", "yaml": yaml_str}
    finally:
        api.api_client.close()


def scale_tkc_cluster(
    si: ServiceInstance,
    name: str,
    namespace: str,
    worker_count: int,
    pool_name: str | None = None,
) -> dict:
    """Scale TKC worker node count.

    Merge-patch semantics REPLACE the machineDeployments list wholesale, so we
    GET the cluster first, update only the requested pool's replicas, and PATCH
    the full preserved list — otherwise the patch would wipe each pool's
    ``class`` field and drop every other node pool.

    Args:
        pool_name: machineDeployment to scale. Defaults to the first existing
            pool (NOT a hardcoded name).
    """
    if worker_count < 1:
        raise ValueError(
            f"worker_count must be >= 1, got {worker_count}. Pass worker_count=1 "
            f"or more to scale_tkc_cluster; to remove the cluster entirely use "
            f"delete_tkc_cluster."
        )

    import kubernetes as k8s

    version = _resolve_tkc_version(si, namespace)
    api = _get_custom_objects_api(si, namespace)
    try:
        try:
            cluster = api.get_namespaced_custom_object(
                group=_TKC_GROUP, version=version,
                namespace=namespace, plural=_TKC_PLURAL, name=name,
            )
        except k8s.client.exceptions.ApiException as e:
            raise _translate_api_exception(si, e, resource=name, namespace=namespace) from e

        pools = (
            cluster.get("spec", {})
            .get("topology", {})
            .get("workers", {})
            .get("machineDeployments")
            or []
        )
        if not pools:
            raise VksApiError(
                f"TKC cluster '{name}' has no machineDeployments to scale — "
                "is it fully provisioned? Run get_tkc_cluster to inspect."
            )

        if pool_name is None:
            idx = 0
        else:
            available = [p.get("name") for p in pools]
            try:
                idx = available.index(pool_name)
            except ValueError:
                raise VksApiError(
                    f"Node pool '{pool_name}' not found in TKC cluster "
                    f"'{name}'. Available pools: {', '.join(str(a) for a in available)}. "
                    f"Copy one of those into pool_name, or omit pool_name so "
                    f"scale_tkc_cluster scales the first pool."
                ) from None

        # New list, new dict for the changed pool — preserve class and all
        # other pools untouched (immutability: do not mutate the GET result).
        new_pools = [
            {**p, "replicas": worker_count} if i == idx else p
            for i, p in enumerate(pools)
        ]
        patch = {
            "spec": {
                "topology": {"workers": {"machineDeployments": new_pools}}
            }
        }
        try:
            api.patch_namespaced_custom_object(
                group=_TKC_GROUP, version=version,
                namespace=namespace, plural=_TKC_PLURAL, name=name, body=patch,
            )
        except k8s.client.exceptions.ApiException as e:
            raise _translate_api_exception(si, e, resource=name, namespace=namespace) from e
        return {
            "name": name,
            "namespace": namespace,
            "pool": new_pools[idx].get("name"),
            "worker_count": worker_count,
            "status": "scaling",
        }
    finally:
        api.api_client.close()


def upgrade_tkc_cluster(
    si: ServiceInstance, name: str, namespace: str, k8s_version: str
) -> dict:
    """Upgrade TKC cluster K8s version."""
    import kubernetes as k8s

    version = _resolve_tkc_version(si, namespace)
    api = _get_custom_objects_api(si, namespace)
    try:
        patch = {"spec": {"topology": {"version": k8s_version}}}
        try:
            api.patch_namespaced_custom_object(
                group=_TKC_GROUP, version=version,
                namespace=namespace, plural=_TKC_PLURAL, name=name, body=patch,
            )
        except k8s.client.exceptions.ApiException as e:
            raise _translate_api_exception(si, e, resource=name, namespace=namespace) from e
        return {"name": name, "namespace": namespace, "new_version": k8s_version, "status": "upgrading"}
    finally:
        api.api_client.close()


def _check_running_workloads(si: ServiceInstance, name: str, namespace: str) -> list[dict]:
    """Check for running Deployments/StatefulSets in the TKC cluster.

    Loads the TKC kubeconfig from an in-memory dict to keep the bearer
    token off disk.
    """
    from vmware_vks.ops.kubeconfig import build_tkc_kubeconfig
    import kubernetes as k8s

    try:
        cfg_dict = build_tkc_kubeconfig(si, name, namespace)
        client_cfg = k8s.client.Configuration()
        k8s.config.load_kube_config_from_dict(
            config_dict=cfg_dict, client_configuration=client_cfg
        )
        api_client = k8s.client.ApiClient(configuration=client_cfg)
        try:
            apps_api = k8s.client.AppsV1Api(api_client)
            workloads = []
            for deploy in _list_all_typed(apps_api.list_deployment_for_all_namespaces):
                if deploy.status.ready_replicas and deploy.status.ready_replicas > 0:
                    workloads.append({
                        "kind": "Deployment",
                        "name": deploy.metadata.name,
                        "namespace": deploy.metadata.namespace,
                    })
            for ss in _list_all_typed(apps_api.list_stateful_set_for_all_namespaces):
                if ss.status.ready_replicas and ss.status.ready_replicas > 0:
                    workloads.append({
                        "kind": "StatefulSet",
                        "name": ss.metadata.name,
                        "namespace": ss.metadata.namespace,
                    })
            for ds in _list_all_typed(apps_api.list_daemon_set_for_all_namespaces):
                if ds.status and ds.status.number_ready and ds.status.number_ready > 0:
                    workloads.append({
                        "kind": "DaemonSet",
                        "name": ds.metadata.name,
                        "namespace": ds.metadata.namespace,
                        "ready": ds.status.number_ready,
                    })
            return workloads
        finally:
            api_client.close()
    except Exception as e:
        _log.warning("Could not verify workloads in cluster '%s': %s", name, e)
        raise VksSafetyError(
            f"Cannot verify workloads in TKC cluster '{name}' — the delete is "
            f"refused rather than risk running workloads. Run 'vmware-vks check' "
            f"to diagnose the connection, or re-run delete_tkc_cluster with "
            f"force=True to skip the workload check. Cause: {cause_summary(e)}"
        ) from e


def delete_tkc_cluster(
    si: ServiceInstance,
    name: str,
    namespace: str,
    confirmed: bool = False,
    dry_run: bool = True,
    force: bool = False,
) -> dict:
    """Delete a TKC cluster with workload guard.

    Guards: rejects if workloads running (unless force=True);
    confirmed=True required for the actual delete. dry_run is evaluated
    BEFORE confirmed — a preview never needs confirmation.
    """
    if not force:
        workloads = _check_running_workloads(si, name, namespace)
        if workloads:
            raise VksSafetyError(
                f"Cannot delete TKC cluster '{name}': {len(workloads)} running "
                f"workload(s) detected. Remove them first — get_tkc_kubeconfig "
                f"gives you a kubeconfig to drain the cluster — or re-run "
                f"delete_tkc_cluster with force=True to delete anyway. Running: "
                f"{', '.join(w['kind'] + '/' + w['name'] for w in workloads[:5])}."
            )

    if dry_run:
        return {
            "dry_run": True,
            "action": "delete_tkc_cluster",
            "name": name,
            "namespace": namespace,
            "warning": "This will permanently delete the TKC cluster.",
        }

    if not confirmed:
        raise ValueError(
            f"confirmed=True required to delete TKC cluster '{name}'. Re-run "
            f"delete_tkc_cluster with confirmed=True to proceed, or with "
            f"dry_run=True to preview what would be deleted."
        )

    import kubernetes as k8s

    version = _resolve_tkc_version(si, namespace)
    api = _get_custom_objects_api(si, namespace)
    try:
        try:
            api.delete_namespaced_custom_object(
                group=_TKC_GROUP, version=version,
                namespace=namespace, plural=_TKC_PLURAL, name=name,
            )
        except k8s.client.exceptions.ApiException as e:
            raise _translate_api_exception(si, e, resource=name, namespace=namespace) from e
        return {"name": name, "namespace": namespace, "status": "deleting"}
    finally:
        api.api_client.close()
