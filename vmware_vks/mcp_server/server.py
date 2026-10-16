"""MCP server for VMware VKS (vSphere with Tanzu).

Exposes Supervisor and Namespace management via MCP stdio transport.

Tool categories
---------------
* **Read-only**: check_vks_compatibility, get_supervisor_status,
  list_supervisor_storage_policies, list_namespaces, get_namespace, list_vm_classes,
  get_access_info
* **Write, gated** (``confirm=False`` previews the blast radius and changes
  nothing): create_namespace, delete_namespace, create_tkc_cluster,
  delete_tkc_cluster — see ``vmware_vks.ops.gate``
* **Write**: update_namespace, scale_tkc_cluster, upgrade_tkc_cluster

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
import ssl
import threading
from typing import Optional

from mcp.server.fastmcp import FastMCP
from vmware_policy import (
    describe_tool_parameters,
    sanitize,
    vmware_tool,
)
from vmware_policy.access import unknown_tool_message, vsphere_access_info

from vmware_vks import __version__
from vmware_vks.access_requirements import REQUIREMENTS
from vmware_vks.config import AppConfig, ConfigError, load_config
from vmware_vks.connection import NOT_AUTHENTICATED_HINT, ConnectionManager
from vmware_vks.errors import VksError
from vmware_vks.notify.audit import AuditLogger
from vmware_vks.ops import gate as _gate

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
    # Authored here, so nothing from the server travels (AIops#47).
    from pyVmomi import vim

    if isinstance(exc, vim.fault.NotAuthenticated):
        return NOT_AUTHENTICATED_HINT
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


def _names_its_own_remedy(exc: Exception) -> bool:
    """The not-authenticated error (AIops#47) already says what to do, and
    "confirm ... credentials" beside it would contradict it."""
    from pyVmomi import vim

    return isinstance(exc, vim.fault.NotAuthenticated)


def _tool_error(exc: Exception) -> dict:
    """One error payload for every tool, with the connectivity hint only when it fits.

    The same line -- "Run 'vmware-vks check' to verify connectivity." -- was
    pasted onto sixteen handlers, and it is wrong more often than it is right.
    ``get_supervisor_status`` on a vCenter without Workload Management returns
    vCenter's own precise sentence, "Cluster with identifier domain-c9 does not
    have Workloads enabled", and then told the reader to go check connectivity
    that was never broken -- ``vmware-vks check`` passes.

    Worse, the hint sat *beside* errors this skill had already authored a remedy
    for: ``k8s_connection`` raises "no Supervisor is RUNNING, run
    check_vks_compatibility, then get_supervisor_status", and the generic line
    displaced it as the thing an agent acts on.

    So the hint is attached only to errors the skill did not author. Anything
    deriving from ``VksError`` -- which is every error written for an agent to
    read, including the REST layer's translated ones -- carries its own next
    step and gets no second, contradictory one.
    """
    if isinstance(exc, VksError) or _names_its_own_remedy(exc):
        return {"error": _safe_error(exc, "vks")}
    return {
        "error": _safe_error(exc, "vks"),
        "hint": (
            "This is not an error this skill authored, so there is no specific "
            "remedy. Run `vmware-vks check` to confirm the vCenter connection "
            "and credentials, then retry."
        ),
    }



_BASE_INSTRUCTIONS = (
    "vSphere Kubernetes Service (VKS): check VKS compatibility, manage vSphere "
    "Namespaces and TanzuKubernetesCluster lifecycle, and fetch kubeconfigs."
)

_TARGET_RULE = (
    " Choosing a target: every tool that reaches vSphere takes `target`. Choose it "
    "from what the user asked — the vCenter, environment or cluster they named. "
    "Each target is a vCenter running a Supervisor, and a Namespace or TKC exists "
    "in exactly one of them, so a name that resolves under one target need not "
    "exist under another. If the request does not say which, and more than one "
    "target is configured, ask the user which one before querying rather than "
    "reporting a Namespace or cluster as missing. A result does not name the target "
    "it came from, so say in the answer which `target` you passed."
)

# Every listed target is a vCenter: this skill's TargetConfig has no `type` field
# at all (config.py cannot express another kind), so the word is a constant here
# rather than a value read from the file — there is nothing for it to drift from.
# It is kept so the listing reads in the family's `name (type, host)` shape.
_TARGET_KIND = "vcenter"


def _target_listing(cfg: AppConfig) -> str:
    """The configured targets as ``name (vcenter, host[, default])`` — or why there are none.

    A config that loads with zero targets is *readable*, just empty: that is a
    fresh install before ``vmware-vks init``. It needs a different remedy from a config
    that could not be read at all, so the two must not collapse into one sentence
    — and neither may collapse into silence.

    Called from inside the caller's ``try`` on purpose: ``AppConfig.default_target``
    raises when the target list is empty, so the empty case is answered here before
    that property is ever asked, and anything unforeseen still lands on the caller's
    fallback instead of stopping the server.
    """
    if not cfg.targets:
        # The documented default path. ``VMWARE_VKS_CONFIG`` can point somewhere else;
        # ``vmware-vks doctor`` reports which file it actually resolved, and this
        # one-line remedy does not try to restate that.
        return "none yet — add one under `targets:` in ~/.vmware-vks/config.yaml"
    default_name = cfg.default_target.name
    return "; ".join(
        f"{t.name} ({_TARGET_KIND}, {t.host}"
        f"{', default' if t.name == default_name else ''})"
        for t in cfg.targets
    )


def _target_instructions() -> str:
    """Server instructions that name the configured targets and how to choose one.

    Until this existed the client was never told which targets are configured, so
    it called every tool with no ``target`` and got whichever one the connection
    layer defaults to. Monitor shipped that way until 2026-09-15: with a standalone
    ESXi host first in the file, "how many VMs does the vCenter have" was answered
    from that host, confidently and wrongly. Here the same silence answers "does
    namespace X exist" from the wrong vCenter, and a Namespace that does exist
    reads as absent.

    The listing is built from the loaded config rather than written out as a
    sentence, so it cannot drift the day the operator edits their file. The
    default is marked by comparing against ``default_target``, which is what
    :meth:`ConnectionManager.connect` itself falls back to when ``target`` is
    omitted — not a guess about which entry comes first.

    Never raises: a missing or broken config must not stop the server from
    starting. The tools report that error themselves, each with its own remedy.
    """
    try:
        detail = _target_listing(load_config())
    except Exception as exc:  # noqa: BLE001 — instructions are advisory, startup is not
        # Say so *under the marker* rather than dropping the listing. Shown no
        # listing at all, a client cannot tell "this skill has no targets" from
        # "this skill could not read them", and the first reading is the one that
        # produces a confident answer about a system nobody chose. Only the
        # exception's type name is interpolated — its text quotes the config path.
        detail = f"could not be read ({type(exc).__name__}) — run `vmware-vks doctor`"
    return f"{_BASE_INSTRUCTIONS} Configured targets: {detail}.{_TARGET_RULE}"


mcp = FastMCP("vmware-vks", instructions=_target_instructions())

# FastMCP takes no version argument and leaves the lowlevel server's at
# None, which makes `initialize` answer with the MCP SDK's version rather
# than ours. Set it so a client can tell which release it is talking to.
mcp._mcp_server.version = __version__
_audit = AuditLogger()

_conn_mgr: Optional[ConnectionManager] = None
# Calls run on worker threads side by side (HLD §16 D-13(2)); two first calls
# racing here would each build a manager, and one manager's sessions would be
# orphaned — open logins nobody pools or closes.
_conn_mgr_lock = threading.Lock()


def _get_conn_mgr() -> ConnectionManager:
    global _conn_mgr
    with _conn_mgr_lock:
        if _conn_mgr is None:
            # No env-var read here: load_config resolves the path (explicit arg,
            # then the environment, then the default). This copy was part of the
            # reason the server and the CLI opened different files — load_config
            # did not look at the variable at all (形态 #6).
            config = load_config()
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
        return _tool_error(e)


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
        return _tool_error(e)


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
        return _tool_error(e)


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
        return _tool_error(e)


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
        return _tool_error(e)


def _acted_undo(action: str, tool: str, note: str, keys: tuple[str, ...]):
    """An undo callable that fires only when the call really ``action``-ed.

    vmware-policy >= 1.17.0 already skips undo for a ``{"action": "preview"}``
    result; this also covers a ``noop`` or any other non-acting answer, so no
    token ever offers to delete something that was never created.
    """
    def _undo(params: dict, result: object) -> Optional[dict]:
        if not (isinstance(result, dict) and result.get("action") == action):
            return None
        return {
            "tool": tool,
            "params": {**{k: params.get(k) for k in keys}, "confirm": True},
            "skill": "vks",
            "note": note,
        }
    return _undo


def _gate_error(e: Exception, tool: str, hint: str, decision: Optional[_gate.Decision]) -> dict:
    """The error envelope for a gated tool, keeping the deprecation note."""
    payload = {"error": _safe_error(e, tool), "hint": hint}
    radius = getattr(e, "blast_radius", None)
    if isinstance(e, _gate.GateRefusedError) and radius is not None:
        payload["blast_radius"] = radius
    if decision is not None and decision.deprecated:
        payload["deprecated"] = decision.deprecated
    return payload


def _with_note(payload: dict, decision: _gate.Decision) -> dict:
    return {**payload, "deprecated": decision.deprecated} if decision.deprecated else payload


def _preview(radius: dict, decision: _gate.Decision, verb: str) -> dict:
    """The L2 answer to a call that did not confirm: the radius, nothing changed."""
    return _with_note({
        "action": "preview",
        "blast_radius": radius,
        "hint": f"Nothing was {verb}d. Show blast_radius to the user; to {verb}, "
                "re-run with confirm=True.",
    }, decision)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(
    risk_level="medium",
    undo=_acted_undo(
        "created", "delete_namespace",
        "Inverse of create_namespace: delete the namespace.", ("name", "target"),
    ),
)
def create_namespace(
    name: str,
    cluster_id: str,
    storage_policy: str,
    cpu_limit: Optional[int] = None,
    memory_limit_mib: Optional[int] = None,
    description: str = "",
    confirm: bool = False,
    dry_run: Optional[bool] = None,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Create a vSphere Namespace on a Supervisor Cluster.

    Without confirm=True this only previews: it returns blast_radius (the spec
    that would be applied, and whether the name is already taken) and creates
    nothing. Show it to the user and get their decision. Do not set
    confirm=True on your own because the user asked earlier — they have not
    seen the preview yet. confirm=True returns {action: "created", namespace,
    status, cluster, blast_radius}; it is refused when the name is taken (use
    update_namespace) or the existing names could not be read. Confirm with
    get_namespace afterwards.

    Args:
        name: Namespace name (lowercase, no spaces).
        cluster_id: Supervisor MoRef (from check_vks_compatibility).
        storage_policy: Policy ID (from list_supervisor_storage_policies).
        cpu_limit: MHz. Omit for no limit.
        memory_limit_mib: MiB. Omit for no limit.
        description: Free-text label. Omit for none.
        confirm: False (default) returns the blast radius and changes nothing. True applies it.
        dry_run: Deprecated alias for confirm; removed in the next minor release.
            dry_run=False acts, dry_run=True previews.
        target: vCenter in config.yaml; omit for the default.
    """
    from vmware_vks.ops import namespace as _ns
    t = target or "default"
    params = {"cluster_id": cluster_id, "storage_policy": storage_policy}
    decision = _gate.decide_create(confirm, dry_run)
    try:
        si = _get_si(target)
        spec = _ns.create_namespace(
            si, name=name, cluster_id=cluster_id, storage_policy=storage_policy,
            cpu_limit=cpu_limit, memory_limit_mib=memory_limit_mib,
            description=description, dry_run=True,
        )["spec"]
        radius = _gate.measure_namespace_create(si, name, spec)
        if not decision.act:
            return _preview(radius, decision, "create")
        _gate.refuse_if_blocked("create_namespace", name, radius)
        result = _ns.create_namespace(
            si, name=name, cluster_id=cluster_id, storage_policy=storage_policy,
            cpu_limit=cpu_limit, memory_limit_mib=memory_limit_mib,
            description=description, dry_run=False,
        )
    except Exception as e:
        if decision.act:
            _audit.log(
                target=t, operation="create_namespace",
                resource=name, parameters=params,
                result=f"error: {sanitize(str(e), 200)}",
            )
        return _gate_error(e, "create_namespace", (
            "Verify cluster_id with check_vks_compatibility and "
            "storage_policy with list_supervisor_storage_policies; "
            "list_namespaces shows whether the name is already taken."
        ), decision)
    _audit.log(
        target=t, operation="create_namespace",
        resource=name, parameters=params,
        result="success",
    )
    return _with_note({"action": "created", **result, "blast_radius": radius}, decision)


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
    confirm: bool = False,
    confirmed: Optional[bool] = None,
    dry_run: Optional[bool] = None,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Delete a vSphere Namespace and everything inside it (irreversible).

    Without confirm=True this only previews: it returns blast_radius (the
    namespace, and the TKC clusters, VMs and PVCs inside it, with counts and
    names) and deletes nothing. Show that to the user and get their explicit
    decision. Do not set confirm=True on your own because the user said
    "delete" earlier: they have not seen what it destroys yet.

    confirm=True returns {action: "deleted", namespace, status, blast_radius}.
    Refused while TKC clusters exist inside (run delete_tkc_cluster on each),
    and whenever the clusters, VMs or PVCs inside could not be read. Prefer
    update_namespace to only change quotas.

    Args:
        name: Namespace name to delete (discover via list_namespaces).
        confirm: False (default) returns the blast radius and changes nothing. True applies it.
        confirmed: Deprecated alias for confirm; removed in the next minor release.
            Acts only together with dry_run=False; confirmed=False previews.
        dry_run: Deprecated alias for confirm; removed in the next minor release.
            dry_run=True previews.
        target: vCenter in config.yaml; omit for the default.
    """
    from vmware_vks.ops import namespace as _ns
    t = target or "default"
    decision = _gate.decide_delete(confirm, confirmed, dry_run)
    try:
        si = _get_si(target)
        radius = _gate.measure_namespace_delete(si, name)
        if not decision.act:
            return _preview(radius, decision, "delete")
        _gate.refuse_if_blocked("delete_namespace", name, radius)
        result = _ns.delete_namespace(si, name, confirmed=True, dry_run=False)
    except Exception as e:
        if decision.act:
            _audit.log(target=t, operation="delete_namespace",
                       resource=name, parameters={}, result=f"error: {sanitize(str(e), 200)}")
        return _gate_error(e, "delete_namespace", (
            "Run list_tkc_clusters — clusters inside the namespace block "
            "the delete — and get_namespace to confirm it still exists."
        ), decision)
    _audit.log(target=t, operation="delete_namespace",
               resource=name, parameters={}, result="success")
    return _with_note({"action": "deleted", **result, "blast_radius": radius}, decision)


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
        return _tool_error(e)


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
    issue_tkc_kubeconfig for access. 'clusters' is a deprecated pre-1.8.0 alias
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
        return _tool_error(e)


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
        return _tool_error(e)


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
        return _tool_error(e)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(
    risk_level="medium",
    undo=_acted_undo(
        "created", "delete_tkc_cluster",
        "Inverse of create_tkc_cluster: delete the TKC cluster.",
        ("name", "namespace", "target"),
    ),
)
def create_tkc_cluster(
    name: str,
    namespace: str,
    k8s_version: str,
    vm_class: str,
    control_plane_count: int = 1,
    worker_count: int = 3,
    storage_class: str = "vsphere-storage",
    confirm: bool = False,
    dry_run: Optional[bool] = None,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Create a TanzuKubernetesCluster in a vSphere Namespace.

    Without confirm=True this only previews: it returns blast_radius (node
    counts, VM class, the YAML manifest, and whether the name is taken) and
    creates nothing. Show it to the user and get their decision. Do not set
    confirm=True on your own because the user asked earlier — they have not
    seen the preview yet. confirm=True returns {action: "created", name,
    namespace, status: "creating", yaml, blast_radius} and provisions in the
    background — poll get_tkc_cluster until phase is running. Refused when the
    name is taken or the namespace's clusters could not be read. Call
    get_tkc_available_versions for k8s_version and list_vm_classes first.

    Args:
        name: Cluster name.
        namespace: Must already exist (see list_namespaces).
        k8s_version: e.g. 'v1.28.4+vmware.1'.
        vm_class: Node sizing, e.g. 'best-effort-large'.
        control_plane_count: 1 or 3.
        worker_count: Worker nodes (>= 1).
        storage_class: Storage class.
        confirm: False (default) returns the blast radius and changes nothing. True applies it.
        dry_run: Deprecated alias for confirm; removed in the next minor release.
            dry_run=False acts, dry_run=True previews.
        target: vCenter in config.yaml; omit for the default.
    """
    from vmware_vks.ops import tkc as _tkc
    t = target or "default"
    params = {"k8s_version": k8s_version, "vm_class": vm_class, "workers": worker_count}
    decision = _gate.decide_create(confirm, dry_run)
    try:
        si = _get_si(target)
        radius = _gate.measure_tkc_create(
            si, name, namespace, k8s_version, vm_class,
            control_plane_count, worker_count, storage_class,
        )
        if not decision.act:
            return _preview(radius, decision, "create")
        _gate.refuse_if_blocked("create_tkc_cluster", name, radius)
        result = _tkc.create_tkc_cluster(
            si, name=name, namespace=namespace, k8s_version=k8s_version,
            vm_class=vm_class, control_plane_count=control_plane_count,
            worker_count=worker_count, storage_class=storage_class, dry_run=False,
        )
    except Exception as e:
        if decision.act:
            _audit.log(
                target=t, operation="create_tkc_cluster",
                resource=f"{namespace}/{name}",
                parameters=params,
                result=f"error: {sanitize(str(e), 200)}",
            )
        return _gate_error(e, "create_tkc_cluster", (
            "Verify k8s_version with get_tkc_available_versions and "
            "vm_class with list_vm_classes; the namespace must already "
            "exist (list_namespaces)."
        ), decision)
    _audit.log(
        target=t, operation="create_tkc_cluster",
        resource=f"{namespace}/{name}",
        parameters=params,
        result="success",
    )
    return _with_note({"action": "created", **result, "blast_radius": radius}, decision)


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
    confirm: bool = False,
    confirmed: Optional[bool] = None,
    dry_run: Optional[bool] = None,
    force: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Delete a TKC cluster and all of its nodes (irreversible).

    Without confirm=True this only previews: it returns blast_radius (control
    plane and worker node counts per pool, and the running Deployments,
    StatefulSets and DaemonSets) and deletes nothing. Show that to the user and
    get their explicit decision. Do not set confirm=True on your own because
    the user said "delete" earlier: they have not seen what it destroys yet.

    confirm=True returns {action: "deleted", name, namespace, status:
    "deleting", blast_radius}. Refused while workloads run (drain them, or pass
    force=True, which skips the workload check entirely) and whenever the node
    counts or workloads could not be read. Use scale_tkc_cluster instead for
    fewer nodes. Empty a namespace of TKC clusters before delete_namespace
    accepts it.

    Args:
        name: Cluster name (via list_tkc_clusters).
        namespace: Namespace holding it.
        confirm: False (default) returns the blast radius and changes nothing. True applies it.
        confirmed: Deprecated alias for confirm; removed in the next minor release.
            Acts only together with dry_run=False; confirmed=False previews.
        dry_run: Deprecated alias for confirm; removed in the next minor release.
            dry_run=True previews.
        force: Skip the running-workload check (dangerous).
        target: vCenter in config.yaml; omit for the default.
    """
    from vmware_vks.ops import tkc as _tkc
    t = target or "default"
    decision = _gate.decide_delete(confirm, confirmed, dry_run)
    try:
        si = _get_si(target)
        radius = _gate.measure_tkc_delete(si, name, namespace, force)
        if not decision.act:
            return _preview(radius, decision, "delete")
        _gate.refuse_if_blocked("delete_tkc_cluster", f"{namespace}/{name}", radius)
        # The gate has just read the workloads (or force skips them); the
        # executor's own check would repeat the read, so it is told to skip it.
        result = _tkc.delete_tkc_cluster(
            si, name, namespace, confirmed=True, dry_run=False, force=True,
        )
    except Exception as e:
        if decision.act:
            _audit.log(target=t, operation="delete_tkc_cluster",
                       resource=f"{namespace}/{name}", parameters={"force": force},
                       result=f"error: {sanitize(str(e), 200)}")
        return _gate_error(e, "delete_tkc_cluster", (
            "Run get_tkc_cluster for the cluster's phase and conditions, "
            "or list_tkc_clusters to confirm name and namespace. Preview "
            "delete_tkc_cluster (without confirm) to see the running "
            "workloads that block it, or pass force=True to skip the check."
        ), decision)
    _audit.log(target=t, operation="delete_tkc_cluster",
               resource=f"{namespace}/{name}", parameters={"force": force},
               result="success")
    return _with_note({"action": "deleted", **result, "blast_radius": radius}, decision)


# ---------------------------------------------------------------------------
# Access tools
# ---------------------------------------------------------------------------

# Credential access, not a read: readOnlyHint is False and risk_level is
# "medium" for both kubeconfig tools. readOnlyHint is what an MCP client uses to
# decide whether to run a tool without asking, and this one hands back a live
# Supervisor bearer token (and, with output_path, truncates a caller-chosen
# file). It was annotated read-only until 2026-09-11 while its TKC sibling was
# not — the reason given for the sibling (output_path writes a file) became
# true of this tool too when output_path was added, and the annotation stayed.
@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="medium", sensitive_result=True)
def issue_supervisor_kubeconfig(
    namespace: str,
    output_path: Optional[str] = None,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Credential access: issue a kubeconfig for the Supervisor K8s API.

    Call only when the user explicitly asks for this kubeconfig; never as a
    side step. Returns {namespace, kubeconfig} as a YAML string, or
    {namespace, written_to} when output_path is given. The kubeconfig embeds
    a Supervisor bearer token (JWT from /wcp/login) that acts as the
    configured vCenter account until the JWT expires (typically hours; not
    tied to this process) — always pass output_path so the token never
    enters agent context, and report only the path. The file is created
    owner-only (0600). Use issue_tkc_kubeconfig instead to reach workloads
    inside a TKC cluster.

    Args:
        namespace: vSphere Namespace to set as the kubeconfig context.
        output_path: File to write, e.g. '~/.kube/supervisor.yaml'. Omit to
            return the kubeconfig inline. Creates parent directories and
            truncates the file.
        target: vCenter in config.yaml; omit for the default.
    """
    try:
        from pathlib import Path
        si = _get_si(target)
        from vmware_vks.ops import kubeconfig as _kc
        path = Path(output_path).expanduser() if output_path else None
        return _kc.write_supervisor_kubeconfig(si, namespace, output_path=path)
    except Exception as e:
        return _tool_error(e)


# destructiveHint is False: this reads the managed cluster and destroys nothing
# in it. It stays [WRITE] and sensitive_result=True, because with `output_path`
# it truncates a file the caller named and the content is a live token — but the
# family's "destructive tools double-confirm in the CLI" gate is about destroying
# managed infrastructure, and demanding two prompts before fetching a kubeconfig
# to a path the operator just typed is friction without a hazard behind it.
@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="medium", sensitive_result=True)
def issue_tkc_kubeconfig(
    name: str,
    namespace: str,
    output_path: Optional[str] = None,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Credential access: issue a kubeconfig for one TKC cluster.

    Call only when the user explicitly asks for this kubeconfig; never as a
    side step. Returns {cluster, kubeconfig}, or {cluster, written_to} when
    output_path is given. The kubeconfig embeds a Supervisor bearer token
    (JWT from /wcp/login) that acts as the configured vCenter account until
    the JWT expires (typically hours; not tied to this process) — always pass
    output_path so the token never enters agent context, and report only the path. Nothing in
    the managed cluster changes, but output_path creates parent directories
    and truncates the named file (owner-only, 0600), so
    output_path='~/.kube/config' replaces the user's own kubeconfig. Run
    list_tkc_clusters first for name and namespace; use
    issue_supervisor_kubeconfig instead for Supervisor-level access.

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
        return _tool_error(e)


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
        return _tool_error(e)


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
        return _tool_error(e)


# ---------------------------------------------------------------------------
# VM Service tools (vm-operator CRDs; served version discovered at runtime)
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
def list_vm_service_snapshots(namespace: str, target: Optional[str] = None) -> dict:
    """[READ] List VirtualMachineSnapshot objects in a vSphere Namespace.

    VM Service snapshots (vmoperator.vmware.com CRD, new at v1alpha5) via the
    Supervisor K8s API — the served CRD version is discovered at runtime, not
    hardcoded. Returns the family list envelope: items of {name, namespace,
    vm_name, created, ready} plus returned/total/truncated (walked to
    completion, so truncated is always false) and served_version. If the
    Supervisor is older than v1alpha5 the error names the required version. Run
    list_namespaces first for the namespace; use list_vm_service_network_interfaces for
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
        return _tool_error(e)


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
def list_vm_service_groups(namespace: str, target: Optional[str] = None) -> dict:
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
        return _tool_error(e)


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
def list_vm_service_network_interfaces(
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
        return _tool_error(e)


# ---------------------------------------------------------------------------
# Access self-diagnosis (HLD §17 D-17)
# ---------------------------------------------------------------------------


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
def get_access_info(tool: Optional[str] = None, target: Optional[str] = None) -> dict:
    """[READ] Which account this server uses, and whether it holds each tool's vSphere privileges.

    Use this when a tool fails with a permission error, or before a write, to
    check what the configured account may do. Asks vCenter about the current
    session (needs only System.View, so any account can ask about itself).
    Returns target, account, the privilege states, and for one tool its
    requires/verdict/meaning; without tool, every tool grouped by verdict.
    Gotchas: privileges are checked on the root folder only, and a verdict of
    not_granted_at_root is NOT "no access" — it may be granted on a specific
    cluster. undetermined means vCenter did not answer; never read it as
    granted. Kubernetes calls (TKC, VM Service, kubeconfigs) are authorized by
    the namespace's view/edit/owner permission, which this cannot check, so
    those tools report undetermined. Namespace writes need Namespaces.Configure.

    Args:
        tool: One tool name on this server, e.g. "create_namespace". Omit to summarize every tool.
        target: vCenter in config.yaml; omit for the default.
    """
    unknown = unknown_tool_message(tool, REQUIREMENTS)  # no login for a misspelled name
    if unknown:
        return {"error": unknown}
    try:
        mgr = _get_conn_mgr()
        name = mgr.resolve_target_name(target)
        si = mgr.connect(name)
        return vsphere_access_info(si, target=name, requirements=REQUIREMENTS, tool=tool)
    except Exception as e:
        return _tool_error(e)


# ---------------------------------------------------------------------------
# Environment declaration
# ---------------------------------------------------------------------------

# The environment resolver lives in policy_environment so the CLI registers
# it too (its @guarded writes go through the same guard()); importing it here
# registers it for the MCP surface.
from vmware_vks.policy_environment import (  # noqa: F401 — imported to register the resolver, and re-exported
    _cached_config,
    _environment_for,
)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


#: How long a stop signal waits for the session logout before exiting anyway.
#: A logout to a vCenter that answers takes well under a second.
_STOP_LOGOUT_SECONDS = 5.0


def _exit_on_stop_signals() -> None:
    """Turn the signals a client stops this server with into a normal exit.

    Claude Code stops a stdio MCP server with SIGINT and then SIGTERM about a
    millisecond later (measured 2026-09-15). Python's default SIGTERM ends the
    process on the spot, before ``atexit`` runs, so the ``Disconnect`` the
    connection layer registered never happened and every conversation left its
    vCenter session open. ``SystemExit`` is not enough: raised from the handler it unwinds the event
    loop, but interpreter shutdown then waits for anyio's worker thread blocked
    reading stdin, which the client keeps open, so ``atexit`` still never ran
    (independent review, 2026-09-15; a test driving the real stdio loop hung in
    all five skills). So the first stop signal ignores the rest, runs the
    ``atexit`` callbacks, and leaves with ``os._exit`` — nothing waits on that
    thread. The callbacks run on a worker thread with a deadline: pyVmomi
    connects with ``httpConnectionTimeout=None``, so a logout to a vCenter that
    stopped answering, or one waiting on the SOAP stub lock a tool call held
    when the signal landed, otherwise left a server that ignored every stop
    signal and only SIGKILL ended (second independent review, 2026-09-15).
    """
    import atexit
    import os
    import signal
    import threading

    stop_signals = [
        getattr(signal, name) for name in ("SIGINT", "SIGTERM", "SIGHUP") if hasattr(signal, name)
    ]

    def _stop(signum: int, _frame: object) -> None:
        for sig in stop_signals:
            signal.signal(sig, signal.SIG_IGN)
        try:
            logout = threading.Thread(
                target=atexit._run_exitfuncs, name="logout-on-stop", daemon=True
            )
            logout.start()
            logout.join(_STOP_LOGOUT_SECONDS)
            if logout.is_alive():
                # Non-blocking, straight to fd 2: a client that keeps stderr open
                # but stops reading it would otherwise park this write, and the
                # exit, on a full pipe (independent review, 2026-09-15). A
                # message that cannot be written is dropped — exiting matters more.
                message = (
                    f"Session logout did not finish within {_STOP_LOGOUT_SECONDS:.0f}s; "
                    "exiting without it. vCenter or ESXi ends the session when it "
                    "idles out.\n"
                )
                try:
                    os.set_blocking(2, False)
                    os.write(2, message.encode("utf-8", "replace"))
                except OSError:
                    pass
        finally:
            os._exit(128 + signum)

    for sig in stop_signals:
        signal.signal(sig, _stop)


def main(
    expose: Optional[str] = None,
    allow_write: Optional[str] = None,
    transport: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
    cert: Optional[str] = None,
    key: Optional[str] = None,
    public_name: Optional[str] = None,
    token_file: Optional[str] = None,
) -> None:
    logging.basicConfig(level=logging.INFO)
    _exit_on_stop_signals()
    from vmware_policy.serve import run_server
    run_server(
        mcp,
        skill="vks",
        pooled=True,
        expose=expose, allow_write=allow_write, transport=transport, host=host,
        port=port, cert=cert, key=key, public_name=public_name, token_file=token_file,
    )


if __name__ == "__main__":
    main()

# The docstrings above are the schema. `describe_tool_parameters` copies each
# `Args:` entry into the JSON schema an agent actually reads, and closes the
# object. Without it every parameter reaches the model as a bare name and a
# type, which is how a wrong guess becomes an unfiltered result or a silent
# zero-row answer instead of an error (real-hardware round, 2026-08-30).
_DESCRIBED_PARAMS = describe_tool_parameters(mcp._tool_manager._tools)
