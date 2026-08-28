"""MCP server for VMware VKS (vSphere with Tanzu).

Exposes Supervisor and Namespace management via MCP stdio transport.

Tool categories
---------------
* **Read-only**: check_vks_compatibility, get_supervisor_status,
  list_supervisor_storage_policies, list_namespaces, get_namespace, list_vm_classes
* **Write** (dry_run=True by default): create_namespace, update_namespace
* **Write** (confirmed + TKC guard): delete_namespace

Security
--------
* Credentials loaded from ~/.vmware-vks/.env (chmod 600 recommended)
* stdio transport only — no network listener
* delete_namespace rejects if TKC clusters exist inside
* All write operations audited twice: the central ~/.vmware/audit.db (SQLite,
  via the @vmware_tool decorator) plus a local JSON-Lines mirror at
  ~/.vmware-vks/audit.log

Source: https://github.com/vmware-skills/VMware-VKS
"""

import logging
import os
import ssl
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
from vmware_policy import (
    mtime_cached_loader,
    sanitize,
    set_environment_resolver,
    vmware_tool,
)

from vmware_vks.config import CONFIG_FILE, ConfigError, load_config
from vmware_vks.connection import ConnectionManager
from vmware_vks.errors import VksError
from vmware_vks.notify.audit import AuditLogger
from vmware_vks import __version__
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vmware-vks.mcp")

def _safe_error(exc: Exception, tool: str) -> str:
    """Return an agent-safe error string; log full detail server-side only.

    Raw exception text can carry API response bodies, internal paths, or
    host:port pairs. Full traceback goes to the server log; the agent sees only
    a control-char-stripped, length-capped message.

    The rule is a property, not a list: every exception this skill raises on
    purpose passes through, and only genuinely unplanned ones are reduced. That
    is ``VksError`` — the base of every error this skill authors, including
    ``VksApiError`` (already sanitized at the connection layer) and the safety
    guards — plus the builtin validation errors and ``ConfigError``.

    ``ConfigError`` is ``config.py``'s missing-password error, this family's
    most common first-run failure, whose entire remedy is the env var name it
    carries. It is deliberately *not* bare ``OSError``: that also passes through
    ``ssl.SSLCertVerificationError`` (quoting the certificate subject and
    hostname), ``socket.gaierror`` (the host that failed to resolve) and
    the full ``scheme://host:port/path``, none of which were written for an
    agent to read. Every connection path translates them first — ``SmartConnect``
    in ``connection.py``, and the REST and ``/wcp/login`` transports via
    ``errors.connection_failure_message`` — so the diagnostic survives without
    the raw text.

    ``ConnectionError`` is deliberately **not** here. The connection layer used
    to raise the builtin for its authored "could not reach" message, which meant
    allowlisting the type — and that same entry then passed urllib3's own
    ``ConnectionError``, whose text is
    ``HTTPSConnectionPool(host='vc.internal', port=443)``. One type arriving from
    two sources is something an allowlist cannot separate, so the authored
    message now raises ``ConfigError`` like its TLS and DNS siblings.

    ``ssl.SSLError`` is reduced *ahead* of the allowlist rather than left off
    it, because an allowlist structurally cannot express "not this one":
    ``ssl.SSLCertVerificationError`` subclasses ``ValueError`` as well as
    ``OSError``, and ``ValueError`` predates all of this — so narrowing
    ``OSError`` alone would have left the certificate subject and the hostname
    reaching the agent exactly as before. Only ``ssl.SSLError`` needs the
    explicit reduction: ``socket.gaierror`` and ``ConnectionRefusedError``
    have ``OSError`` as their only base and are genuinely withheld by the
    narrowing itself.

    Bare ``RuntimeError`` stays out, even though ``VksError`` subclasses it: the
    same type carries raw text from callers that never intended it to be read
    by an agent, so admitting the base class would admit those too.

    Anything else is reduced to its type — an unplanned exception's text was
    written for a developer reading a traceback, not for an agent choosing what
    to do next, and it is the one that can carry credentials.
    """
    logger.error("Tool %s failed", tool, exc_info=True)
    if isinstance(exc, ssl.SSLError):
        # Ahead of the allowlist on purpose — SSLCertVerificationError is also
        # a ValueError, so no allowlist edit can withhold it.
        return f"{type(exc).__name__}: operation failed."
    if isinstance(
        exc,
        (
            VksError,
            ValueError,
            FileNotFoundError,
            KeyError,
            PermissionError,
                ConfigError,
        ),
    ):
        return sanitize(str(exc), 300)
    return f"{type(exc).__name__}: operation failed."


mcp = FastMCP("VMware VKS")

# FastMCP takes no version argument and leaves the lowlevel server's at
# None, which makes `initialize` answer with the MCP SDK's version rather
# than ours. Set it so a client can tell which release it is talking to.
mcp._mcp_server.version = __version__
_audit = AuditLogger()

_conn_mgr: Optional[ConnectionManager] = None


def _get_conn_mgr() -> ConnectionManager:
    global _conn_mgr
    if _conn_mgr is None:
        config_path = os.environ.get("VMWARE_VKS_CONFIG")
        config = load_config(Path(config_path) if config_path else None)
        _conn_mgr = ConnectionManager(config)
    return _conn_mgr


def _get_si(target: Optional[str] = None):
    return _get_conn_mgr().connect(target)


# ---------------------------------------------------------------------------
# Supervisor tools
# ---------------------------------------------------------------------------

@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
def check_vks_compatibility(target: Optional[str] = None) -> dict:
    """[READ] Check whether this vCenter supports VKS (requires vSphere 8.x+).

    Returns compatible (bool), vcenter_version, wcp_enabled_clusters
    and wcp_clusters ({cluster, status}). Start here: those cluster MoRefs are
    the cluster_id for get_supervisor_status and create_namespace. Only
    reports vCenter-level support — a listed cluster may still be CONFIGURING.

    Args:
        target: vCenter in config.yaml; omit for the default.
    """
    try:
        si = _get_si(target)
        from vmware_vks.ops import supervisor as _sup
        return _sup.check_vks_compatibility(si)
    except Exception as e:
        return {"error": _safe_error(e, "vks"), "hint": "Run 'vmware-vks check' to verify connectivity."}


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
def get_supervisor_status(cluster_id: str, target: Optional[str] = None) -> dict:
    """[READ] Get the health of one Supervisor Cluster (vSphere with Tanzu control plane).

    Returns cluster_id, config_status (RUNNING = healthy, else CONFIGURING /
    ERROR / REMOVING), kubernetes_status (READY / WARNING / ERROR),
    api_server_cluster_endpoint, kubernetes_version (null plus
    kubernetes_version_hint if unavailable), and network_provider. Run
    check_vks_compatibility first for cluster IDs; use this to confirm a
    Supervisor is healthy before create_namespace or create_tkc_cluster.

    Args:
        cluster_id: Compute cluster MoRef, e.g. 'domain-c1' (wcp_clusters
            field of check_vks_compatibility).
        target: vCenter in config.yaml; omit for the default.
    """
    try:
        si = _get_si(target)
        from vmware_vks.ops import supervisor as _sup
        return _sup.get_supervisor_status(si, cluster_id)
    except Exception as e:
        return {"error": _safe_error(e, "vks"), "hint": "Run 'vmware-vks check' to verify connectivity."}


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
def list_supervisor_storage_policies(target: Optional[str] = None) -> dict:
    """[READ] List vCenter storage policies assignable to Supervisor Namespaces.

    Returns the list envelope: items of {policy (ID), name, description} plus
    returned/total/truncated — one call returns them all, so truncated is
    always false. Call this before create_namespace or update_namespace and
    pass the 'policy' ID as their storage_policy. For PVC-level usage use
    list_namespace_storage_usage instead.

    Args:
        target: vCenter in config.yaml; omit for the default.
    """
    try:
        si = _get_si(target)
        from vmware_vks.ops import supervisor as _sup
        return _sup.list_supervisor_storage_policies(si)
    except Exception as e:
        return {"error": _safe_error(e, "vks"), "hint": "Run 'vmware-vks check' to verify connectivity."}


# ---------------------------------------------------------------------------
# Namespace tools
# ---------------------------------------------------------------------------

@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
def list_namespaces(target: Optional[str] = None) -> dict:
    """[READ] List all vSphere Namespaces on the target vCenter with their status.

    Returns the list envelope: items of {namespace, config_status (RUNNING =
    healthy, CONFIGURING, REMOVING, ERROR), description} plus
    returned/total/truncated — one call returns them all, so truncated is
    always false. Start here, then call get_namespace for detail,
    list_tkc_clusters for what runs inside, or update_namespace /
    delete_namespace to change one.

    Args:
        target: vCenter in config.yaml; omit for the default.
    """
    try:
        si = _get_si(target)
        from vmware_vks.ops import namespace as _ns
        return _ns.list_namespaces(si)
    except Exception as e:
        return {"error": _safe_error(e, "vks"), "hint": "Run 'vmware-vks check' to verify connectivity."}


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
def get_namespace(name: str, target: Optional[str] = None) -> dict:
    """[READ] Get detailed configuration for a single vSphere Namespace.

    Returns one raw vCenter namespace object, not the list envelope:
    config_status, description, storage_specs, quotas. Use
    list_namespaces first for the name; follow with
    list_namespace_storage_usage for PVC usage or list_tkc_clusters for the
    clusters inside. Point-in-time only — a CONFIGURING namespace may
    not have quotas applied.

    Args:
        name: Namespace name, e.g. 'dev' (discover via list_namespaces).
        target: vCenter in config.yaml; omit for the default.
    """
    try:
        si = _get_si(target)
        from vmware_vks.ops import namespace as _ns
        return _ns.get_namespace(si, name)
    except Exception as e:
        return {"error": _safe_error(e, "vks"), "hint": "Run 'vmware-vks check' to verify connectivity."}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(
    risk_level="medium",
    undo=lambda params, result: {
        "tool": "delete_namespace",
        "params": {
            "name": params.get("name"),
            "target": params.get("target"),
            "confirmed": True,
            "dry_run": False,
        },
        "skill": "vks",
        "note": "Inverse of create_namespace: delete the namespace.",
    },
)
def create_namespace(
    name: str,
    cluster_id: str,
    storage_policy: str,
    cpu_limit: Optional[int] = None,
    memory_limit_mib: Optional[int] = None,
    description: str = "",
    dry_run: bool = True,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Create a vSphere Namespace on a Supervisor Cluster.

    Returns {namespace, status: "created", cluster}, or {dry_run, spec} — a
    dry run unless dry_run=False. Use update_namespace instead when
    it already exists; confirm with get_namespace afterwards.

    Args:
        name: Namespace name (lowercase, no spaces).
        cluster_id: Supervisor MoRef (from check_vks_compatibility).
        storage_policy: Policy ID (from list_supervisor_storage_policies).
        cpu_limit: MHz. Omit for no limit.
        memory_limit_mib: MiB. Omit for no limit.
        description: Free-text label. Omit for none.
        dry_run: Preview only (default: True).
        target: vCenter in config.yaml; omit for the default.
    """
    from vmware_vks.ops import namespace as _ns
    t = target or "default"
    params = {"cluster_id": cluster_id, "storage_policy": storage_policy}
    try:
        si = _get_si(target)
        result = _ns.create_namespace(
            si, name=name, cluster_id=cluster_id, storage_policy=storage_policy,
            cpu_limit=cpu_limit, memory_limit_mib=memory_limit_mib,
            description=description, dry_run=dry_run,
        )
    except Exception as e:
        if not dry_run:
            _audit.log(
                target=t, operation="create_namespace",
                resource=name, parameters=params,
                result=f"error: {sanitize(str(e), 200)}",
            )
        return {
            "error": _safe_error(e, "create_namespace"),
            "hint": (
                "Verify cluster_id with check_vks_compatibility and "
                "storage_policy with list_supervisor_storage_policies; "
                "list_namespaces shows whether the name is already taken."
            ),
        }
    if not dry_run:
        _audit.log(
            target=t, operation="create_namespace",
            resource=name, parameters=params,
            result="success",
        )
    return result


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
def update_namespace(
    name: str,
    cpu_limit: Optional[int] = None,
    memory_limit_mib: Optional[int] = None,
    storage_policy: Optional[str] = None,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Update resource quotas or storage policy of an existing vSphere Namespace.

    Only the fields you pass are patched; omitting all of them returns status
    "no_changes" without an API call, otherwise {namespace, status:
    "updated"}. Applies immediately — no dry run, no undo. Use this rather
    than create_namespace when the namespace exists; valid storage_policy
    values come from list_supervisor_storage_policies.

    Args:
        name: Existing namespace name (discover via list_namespaces).
        cpu_limit: New CPU limit in MHz. Omit to keep current.
        memory_limit_mib: New memory limit in MiB. Omit to keep current.
        storage_policy: New storage policy ID. Omit to keep current.
        target: vCenter in config.yaml; omit for the default.
    """
    from vmware_vks.ops import namespace as _ns
    t = target or "default"
    try:
        si = _get_si(target)
        result = _ns.update_namespace(si, name, cpu_limit=cpu_limit,
                                      memory_limit_mib=memory_limit_mib,
                                      storage_policy=storage_policy)
    except Exception as e:
        _audit.log(target=t, operation="update_namespace",
                   resource=name, parameters={}, result=f"error: {sanitize(str(e), 200)}")
        return {
            "error": _safe_error(e, "update_namespace"),
            "hint": (
                "Run get_namespace for the namespace's current settings, or "
                "list_supervisor_storage_policies for valid storage_policy "
                "values."
            ),
        }
    _audit.log(target=t, operation="update_namespace",
               resource=name, parameters={}, result="success")
    return result


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="high")
def delete_namespace(
    name: str,
    confirmed: bool = False,
    dry_run: bool = True,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Delete a vSphere Namespace and everything inside it.

    Returns {namespace, status: "deleted"}, or a preview by default. SAFETY:
    refused while TKC clusters exist inside — run list_tkc_clusters to see
    them, then delete_tkc_cluster on each. A dry run unless you pass
    dry_run=False AND confirmed=True. Irreversible; prefer update_namespace to
    only change quotas.

    Args:
        name: Namespace name to delete (discover via list_namespaces).
        confirmed: Must be True to proceed.
        dry_run: Preview only (default: True).
        target: vCenter in config.yaml; omit for the default.
    """
    from vmware_vks.ops import namespace as _ns
    t = target or "default"
    try:
        si = _get_si(target)
        result = _ns.delete_namespace(si, name, confirmed=confirmed, dry_run=dry_run)
    except Exception as e:
        if not dry_run and confirmed:
            _audit.log(target=t, operation="delete_namespace",
                       resource=name, parameters={}, result=f"error: {sanitize(str(e), 200)}")
        return {
            "error": _safe_error(e, "delete_namespace"),
            "hint": (
                "Run list_tkc_clusters — clusters inside the namespace block "
                "the delete — and get_namespace to confirm it still exists."
            ),
        }
    if not dry_run and confirmed:
        _audit.log(target=t, operation="delete_namespace",
                   resource=name, parameters={}, result="success")
    return result


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
def list_vm_classes(target: Optional[str] = None) -> dict:
    """[READ] List VM classes available for sizing TKC cluster nodes.

    Returns the list envelope: items of {id (e.g.
    'best-effort-large'), cpu_count, memory_mb, gpu_count (vGPU + DirectPath
    I/O; 0 if none)} plus returned/total/truncated — one call returns them
    all, so truncated is always false. Call this before create_tkc_cluster and
    pass the chosen 'id' as its vm_class; 'guaranteed-*' classes reserve
    resources, 'best-effort-*' do not.

    Args:
        target: vCenter in config.yaml; omit for the default.
    """
    try:
        si = _get_si(target)
        from vmware_vks.ops import namespace as _ns
        return _ns.list_vm_classes(si)
    except Exception as e:
        return {"error": _safe_error(e, "vks"), "hint": "Run 'vmware-vks check' to verify connectivity."}


# ---------------------------------------------------------------------------
# TKC tools
# ---------------------------------------------------------------------------

@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
def list_tkc_clusters(namespace: Optional[str] = None, target: Optional[str] = None) -> dict:
    """[READ] List TanzuKubernetesCluster (TKC) clusters, optionally in one namespace.

    Returns the family list envelope: {items: [{name, namespace, phase,
    k8s_version}], returned, limit, total, truncated, hint}. The Supervisor
    list is walked to completion, so truncated is always False. Start here, then call get_tkc_cluster for full detail or
    get_tkc_kubeconfig for access. 'clusters' is a deprecated pre-1.8.0 alias
    of 'items', removed in 2.0 — read 'items'.

    Args:
        namespace: vSphere Namespace to filter by. Omit to list every one.
        target: vCenter in config.yaml; omit for the default.
    """
    try:
        si = _get_si(target)
        from vmware_vks.ops import tkc as _tkc
        return _tkc.list_tkc_clusters(si, namespace=namespace)
    except Exception as e:
        return {"error": _safe_error(e, "vks"), "hint": "Run 'vmware-vks check' to verify connectivity."}


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
def get_tkc_cluster(name: str, namespace: str, target: Optional[str] = None) -> dict:
    """[READ] Get detailed status for a single TKC cluster.

    Returns one object, not the list envelope: name, namespace, phase,
    k8s_version, control_plane_replicas, worker_replicas, conditions,
    infrastructure_ready, control_plane_ready. Run list_tkc_clusters first — a
    TKC name is only unique within one namespace. Poll this after
    create_tkc_cluster, scale_tkc_cluster or upgrade_tkc_cluster to watch an
    async change land.

    Args:
        name: Cluster name (via list_tkc_clusters).
        namespace: Namespace holding it.
        target: vCenter in config.yaml; omit for the default.
    """
    try:
        si = _get_si(target)
        from vmware_vks.ops import tkc as _tkc
        return _tkc.get_tkc_cluster(si, name, namespace)
    except Exception as e:
        return {"error": _safe_error(e, "vks"), "hint": "Run 'vmware-vks check' to verify connectivity."}


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
def get_tkc_available_versions(namespace: str, target: Optional[str] = None) -> dict:
    """[READ] List Kubernetes versions (TanzuKubernetesReleases) available on the Supervisor.

    Returns {versions: [{name, version, e.g. 'v1.28.4+vmware.1'}]}, newest
    first. If the TanzuKubernetesRelease API is unavailable it returns an
    empty versions list with error and hint rather than raising. Call this
    before create_tkc_cluster or upgrade_tkc_cluster to pick a valid
    k8s_version.

    Args:
        namespace: vSphere Namespace used to reach the Supervisor K8s API.
        target: vCenter in config.yaml; omit for the default.
    """
    try:
        si = _get_si(target)
        from vmware_vks.ops import tkc as _tkc
        return _tkc.get_tkc_available_versions(si, namespace)
    except Exception as e:
        return {"error": _safe_error(e, "vks"), "hint": "Run 'vmware-vks check' to verify connectivity."}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(
    risk_level="medium",
    undo=lambda params, result: {
        "tool": "delete_tkc_cluster",
        "params": {
            "name": params.get("name"),
            "namespace": params.get("namespace"),
            "target": params.get("target"),
            "confirmed": True,
            "dry_run": False,
        },
        "skill": "vks",
        "note": "Inverse of create_tkc_cluster: delete the TKC cluster.",
    },
)
def create_tkc_cluster(
    name: str,
    namespace: str,
    k8s_version: str,
    vm_class: str,
    control_plane_count: int = 1,
    worker_count: int = 3,
    storage_class: str = "vsphere-storage",
    dry_run: bool = True,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Create a TanzuKubernetesCluster in a vSphere Namespace.

    A dry run unless you pass dry_run=False; it then returns {name, namespace,
    status: "creating", yaml} and provisions in the background — poll
    get_tkc_cluster until phase is running. Call get_tkc_available_versions
    for k8s_version and list_vm_classes for vm_class first.

    Args:
        name: Cluster name.
        namespace: Must already exist (see list_namespaces).
        k8s_version: e.g. 'v1.28.4+vmware.1'.
        vm_class: Node sizing, e.g. 'best-effort-large'.
        control_plane_count: 1 or 3.
        worker_count: Worker nodes (>= 1).
        storage_class: Storage class.
        dry_run: YAML plan only (default: True).
        target: vCenter in config.yaml; omit for the default.
    """
    from vmware_vks.ops import tkc as _tkc
    t = target or "default"
    params = {"k8s_version": k8s_version, "vm_class": vm_class, "workers": worker_count}
    try:
        si = _get_si(target)
        result = _tkc.create_tkc_cluster(
            si, name=name, namespace=namespace, k8s_version=k8s_version,
            vm_class=vm_class, control_plane_count=control_plane_count,
            worker_count=worker_count, storage_class=storage_class, dry_run=dry_run,
        )
    except Exception as e:
        if not dry_run:
            _audit.log(
                target=t, operation="create_tkc_cluster",
                resource=f"{namespace}/{name}",
                parameters=params,
                result=f"error: {sanitize(str(e), 200)}",
            )
        return {
            "error": _safe_error(e, "create_tkc_cluster"),
            "hint": (
                "Verify k8s_version with get_tkc_available_versions and "
                "vm_class with list_vm_classes; the namespace must already "
                "exist (list_namespaces)."
            ),
        }
    if not dry_run:
        _audit.log(
            target=t, operation="create_tkc_cluster",
            resource=f"{namespace}/{name}",
            parameters=params,
            result="success",
        )
    return result


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
def scale_tkc_cluster(
    name: str,
    namespace: str,
    worker_count: int,
    pool_name: Optional[str] = None,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Scale the worker node count of an existing TanzuKubernetesCluster (TKC).

    Asynchronous: returns {name, namespace, pool, worker_count, status:
    "scaling"} immediately — poll get_tkc_cluster to watch nodes appear or
    drain. Scales workers only; use upgrade_tkc_cluster instead for the K8s
    version. Not destructive, but lowering worker_count drains removed nodes.

    Args:
        name: Cluster name (via list_tkc_clusters).
        namespace: Namespace holding it.
        worker_count: Desired total, integer >= 1 (below 1 is rejected).
        pool_name: Node pool (machineDeployment). Omit for the first;
            other pools are always preserved.
        target: vCenter in config.yaml; omit for the default.
    """
    from vmware_vks.ops import tkc as _tkc
    t = target or "default"
    params = {"worker_count": worker_count, "pool_name": pool_name}
    try:
        si = _get_si(target)
        result = _tkc.scale_tkc_cluster(
            si, name, namespace, worker_count, pool_name=pool_name
        )
    except Exception as e:
        _audit.log(target=t, operation="scale_tkc_cluster",
                   resource=f"{namespace}/{name}", parameters=params,
                   result=f"error: {sanitize(str(e), 200)}")
        return {
            "error": _safe_error(e, "scale_tkc_cluster"),
            "hint": (
                "Run get_tkc_cluster for the cluster's current phase and "
                "replica counts. No tool lists node pool names; an unknown "
                "pool_name is rejected with the available names in the error."
            ),
        }
    _audit.log(target=t, operation="scale_tkc_cluster",
               resource=f"{namespace}/{name}", parameters=params,
               result="success")
    return result


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
def upgrade_tkc_cluster(
    name: str, namespace: str, k8s_version: str, target: Optional[str] = None
) -> dict:
    """[WRITE] Upgrade a TKC cluster to a new Kubernetes version.

    Returns {name, namespace, new_version, status: "upgrading"}. Asynchronous
    and irreversible — Kubernetes cannot be downgraded, so poll
    get_tkc_cluster until phase is running. There is no dry run. Use this only
    for the K8s version; prefer scale_tkc_cluster for node counts.

    Args:
        name: Cluster name (via list_tkc_clusters).
        namespace: Namespace holding it.
        k8s_version: Target version from get_tkc_available_versions.
        target: vCenter in config.yaml; omit for the default.
    """
    from vmware_vks.ops import tkc as _tkc
    t = target or "default"
    try:
        si = _get_si(target)
        result = _tkc.upgrade_tkc_cluster(si, name, namespace, k8s_version)
    except Exception as e:
        _audit.log(target=t, operation="upgrade_tkc_cluster",
                   resource=f"{namespace}/{name}", parameters={"k8s_version": k8s_version},
                   result=f"error: {sanitize(str(e), 200)}")
        return {
            "error": _safe_error(e, "upgrade_tkc_cluster"),
            "hint": (
                "Run get_tkc_available_versions for the versions this "
                "Supervisor offers, and get_tkc_cluster for the cluster's "
                "current phase."
            ),
        }
    _audit.log(target=t, operation="upgrade_tkc_cluster",
               resource=f"{namespace}/{name}", parameters={"k8s_version": k8s_version},
               result="success")
    return result


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="high")
def delete_tkc_cluster(
    name: str,
    namespace: str,
    confirmed: bool = False,
    dry_run: bool = True,
    force: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Delete a TKC cluster and all of its nodes.

    Returns {name, namespace, status: "deleting"}, or a preview by default.
    SAFETY: refused while Deployments/StatefulSets run, unless force=True. A
    dry run unless you pass dry_run=False AND confirmed=True. Irreversible —
    use scale_tkc_cluster instead for fewer nodes. Empty a namespace of TKC
    clusters before delete_namespace accepts it.

    Args:
        name: Cluster name (via list_tkc_clusters).
        namespace: Namespace holding it.
        confirmed: Must be True to proceed.
        dry_run: Preview only (default: True).
        force: Skip the workload check (dangerous).
        target: vCenter in config.yaml; omit for the default.
    """
    from vmware_vks.ops import tkc as _tkc
    t = target or "default"
    try:
        si = _get_si(target)
        result = _tkc.delete_tkc_cluster(
            si, name, namespace, confirmed=confirmed, dry_run=dry_run, force=force,
        )
    except Exception as e:
        if not dry_run and confirmed:
            _audit.log(target=t, operation="delete_tkc_cluster",
                       resource=f"{namespace}/{name}", parameters={"force": force},
                       result=f"error: {sanitize(str(e), 200)}")
        return {
            "error": _safe_error(e, "delete_tkc_cluster"),
            "hint": (
                "Run get_tkc_cluster for the cluster's phase and conditions, "
                "or list_tkc_clusters to confirm name and namespace. The "
                "running workloads that block a delete are not listed by any "
                "tool — use get_tkc_kubeconfig and inspect the cluster, or "
                "pass force=True to skip the check."
            ),
        }
    if not dry_run and confirmed:
        _audit.log(target=t, operation="delete_tkc_cluster",
                   resource=f"{namespace}/{name}", parameters={"force": force},
                   result="success")
    return result


# ---------------------------------------------------------------------------
# Access tools
# ---------------------------------------------------------------------------

@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
def get_supervisor_kubeconfig(namespace: str, target: Optional[str] = None) -> dict:
    """[READ] Get a kubeconfig for the Supervisor K8s API endpoint.

    Returns {namespace, kubeconfig} as a YAML string. Use this for
    Supervisor-level access; use get_tkc_kubeconfig instead to reach workloads
    inside a TKC cluster. Security: it carries a short-lived session token —
    treat it as a credential, do not log or share.

    Args:
        namespace: vSphere Namespace to set as the kubeconfig context.
        target: vCenter in config.yaml; omit for the default.
    """
    try:
        si = _get_si(target)
        from vmware_vks.ops import kubeconfig as _kc
        kc_str = _kc.get_supervisor_kubeconfig_str(si, namespace)
        return {"namespace": namespace, "kubeconfig": kc_str}
    except Exception as e:
        return {"error": _safe_error(e, "vks"), "hint": "Run 'vmware-vks check' to verify connectivity."}


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
def get_tkc_kubeconfig(
    name: str,
    namespace: str,
    output_path: Optional[str] = None,
    target: Optional[str] = None,
) -> dict:
    """[READ] Get a kubeconfig for one TKC cluster.

    Returns {cluster, kubeconfig}, or {cluster, written_to} when
    output_path is given. Run list_tkc_clusters first for name and namespace;
    use get_supervisor_kubeconfig instead for Supervisor-level access.
    Security: it carries a short-lived session token — always prefer
    output_path so the credential never enters agent context.

    Args:
        name: TKC cluster name.
        namespace: Namespace holding it.
        output_path: File to write, e.g. '~/.kube/my.yaml'. Omit to return
            the kubeconfig inline.
        target: vCenter in config.yaml; omit for the default.
    """
    try:
        from pathlib import Path
        si = _get_si(target)
        from vmware_vks.ops import kubeconfig as _kc
        path = Path(output_path).expanduser() if output_path else None
        return _kc.write_kubeconfig(si, name, namespace, output_path=path)
    except Exception as e:
        return {"error": _safe_error(e, "vks"), "hint": "Run 'vmware-vks check' to verify connectivity."}


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
def get_harbor_info(target: Optional[str] = None) -> dict:
    """[READ] Get status of the embedded Harbor container registry on the Supervisor.

    Returns {registries: [{id, cluster (Supervisor MoRef), version, url,
    status, storage_used_mb}]}; status and storage come from a detail call and
    are null if it fails. If Harbor is not enabled it returns
    {error, hint} rather than raising. Use it to check registry health or find
    the push URL — it does not list repositories or images. Run
    check_vks_compatibility first if the Supervisor may be down.

    Args:
        target: vCenter in config.yaml; omit for the default.
    """
    try:
        si = _get_si(target)
        from vmware_vks.ops import harbor as _harbor
        return _harbor.get_harbor_info(si)
    except Exception as e:
        return {"error": _safe_error(e, "vks"), "hint": "Run 'vmware-vks check' to verify connectivity."}


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
def list_namespace_storage_usage(namespace: str, target: Optional[str] = None) -> dict:
    """[READ] List PersistentVolumeClaims and storage usage inside one vSphere Namespace.

    Via the Supervisor K8s API. Returns the family list envelope:
    {namespace, items: [{name, namespace, status (Bound / Pending / Lost),
    capacity ('10Gi'), storage_class}], returned, limit, total,
    truncated, hint}. Every PVC comes back in one call, so truncated is always
    False. Run list_namespaces first for the namespace; use
    list_supervisor_storage_policies instead for policy-level rather than
    PVC-level information. 'pvcs' and 'pvc_count' are deprecated pre-1.8.0
    aliases of 'items' and 'returned', removed in 2.0 — read 'items'.

    Args:
        namespace: Namespace to inspect.
        target: vCenter in config.yaml; omit for the default.
    """
    try:
        si = _get_si(target)
        from vmware_vks.ops import storage as _storage
        return _storage.list_namespace_storage_usage(si, namespace)
    except Exception as e:
        return {"error": _safe_error(e, "vks"), "hint": "Run 'vmware-vks check' to verify connectivity."}


# ---------------------------------------------------------------------------
# VM Service tools (vm-operator CRDs; served version discovered at runtime)
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
def list_vm_snapshots(namespace: str, target: Optional[str] = None) -> dict:
    """[READ] List VirtualMachineSnapshot objects in a vSphere Namespace.

    VM Service snapshots (vmoperator.vmware.com CRD, new at v1alpha5) via the
    Supervisor K8s API — the served CRD version is discovered at runtime, not
    hardcoded. Returns the family list envelope: items of {name, namespace,
    vm_name, created, ready} plus returned/total/truncated (walked to
    completion, so truncated is always false) and served_version. If the
    Supervisor is older than v1alpha5 the error names the required version. Run
    list_namespaces first for the namespace; use list_vm_network_interfaces for
    a VM's NICs.

    Args:
        namespace: vSphere Namespace to list snapshots in (via list_namespaces).
        target: vCenter in config.yaml; omit for the default.
    """
    try:
        si = _get_si(target)
        from vmware_vks.ops import vmservice as _vmsvc
        return _vmsvc.list_vm_snapshots(si, namespace)
    except Exception as e:
        return {"error": _safe_error(e, "vks"), "hint": "Run 'vmware-vks check' to verify connectivity."}


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
def list_vm_groups(namespace: str, target: Optional[str] = None) -> dict:
    """[READ] List VirtualMachineGroup objects and their bootOrder in a Namespace.

    VM Service groups (vmoperator.vmware.com CRD, v1alpha4+) via the Supervisor
    K8s API — the served CRD version is discovered at runtime. Returns the
    family list envelope: items of {name, namespace, boot_order, member_count}
    plus returned/total/truncated (walked to completion) and served_version.
    ``boot_order`` mirrors spec.bootOrder: an ordered list of {members:
    [{kind, name}], power_on_delay?}. Older Supervisors without v1alpha4 return
    a teaching error naming the required version.

    Args:
        namespace: vSphere Namespace to list VM groups in (via list_namespaces).
        target: vCenter in config.yaml; omit for the default.
    """
    try:
        si = _get_si(target)
        from vmware_vks.ops import vmservice as _vmsvc
        return _vmsvc.list_vm_groups(si, namespace)
    except Exception as e:
        return {"error": _safe_error(e, "vks"), "hint": "Run 'vmware-vks check' to verify connectivity."}


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
def list_vm_network_interfaces(
    namespace: str, vm_name: str, target: Optional[str] = None
) -> dict:
    """[READ] List the network interfaces (multi-NIC) of one VirtualMachine.

    Reads spec.network.interfaces[] off a single VM Service VirtualMachine
    (vmoperator.vmware.com) via the Supervisor K8s API; the served CRD version
    is discovered at runtime. Returns the family list envelope: items of {name,
    network_name, network_kind, network_api_version} plus
    returned/total/truncated and vm_name/served_version. A VM with no network
    block returns an empty list, not an error. Run list_namespaces for the
    namespace; the VM name comes from your own VM inventory in that namespace.

    Args:
        namespace: vSphere Namespace the VM lives in (via list_namespaces).
        vm_name: Name of the VirtualMachine to read interfaces from.
        target: vCenter in config.yaml; omit for the default.
    """
    try:
        si = _get_si(target)
        from vmware_vks.ops import vmservice as _vmsvc
        return _vmsvc.list_vm_network_interfaces(si, namespace, vm_name)
    except Exception as e:
        return {"error": _safe_error(e, "vks"), "hint": "Run 'vmware-vks check' to verify connectivity."}


# ---------------------------------------------------------------------------
# Environment declaration
# ---------------------------------------------------------------------------


_cached_config = mtime_cached_loader("VMWARE_VKS_CONFIG", CONFIG_FILE, load_config)


def _environment_for(target: Optional[str]) -> str:
    """Report the environment a target declares, for policy scoping.

    Policy rules scope by environment ("irreversible work in production needs a
    second person"), and vmware-policy cannot read this skill's config itself.
    Registering this lookup is what lets those rules fire at all. Reloaded on
    config.yaml mtime change so an edit takes effect without restarting the
    server, and resolved through the same VMWARE_VKS_CONFIG override the
    connection manager uses so both agree on which file is in force. The config
    is cached via :func:`vmware_policy.mtime_cached_loader`, so repeated tool
    calls pay one ``os.stat`` instead of a full YAML parse.
    """
    try:
        return _cached_config().environment_for(target)
    except Exception:  # noqa: BLE001 — an unreadable config means "undeclared"
        return ""


set_environment_resolver(_environment_for)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
