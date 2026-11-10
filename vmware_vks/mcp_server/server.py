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

import sys as _sys
import logging
import ssl
import threading
from typing import Optional

from mcp.server.fastmcp import FastMCP
from vmware_policy import (  # noqa: F401 — compatibility/runtime export
    describe_tool_parameters,
    sanitize,
    vmware_tool,
)
from vmware_policy.access import unknown_tool_message, vsphere_access_info  # noqa: F401 — compatibility/runtime export

from vmware_vks import __version__
from vmware_vks.access_requirements import REQUIREMENTS  # noqa: F401 — compatibility/runtime export
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









# ---------------------------------------------------------------------------
# Namespace tools
# ---------------------------------------------------------------------------





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


def _gate_error(e: Exception, tool: str, hint: str) -> dict:
    """The error envelope for a gated tool, with the blast radius of a refusal."""
    payload = {"error": _safe_error(e, tool), "hint": hint}
    radius = getattr(e, "blast_radius", None)
    if isinstance(e, _gate.GateRefusedError) and radius is not None:
        payload["blast_radius"] = radius
    return payload


def _preview(radius: dict, verb: str) -> dict:
    """The L2 answer to a call that did not confirm: the radius, nothing changed."""
    return {
        "action": "preview",
        "blast_radius": radius,
        "hint": f"Nothing was {verb}d. Show blast_radius to the user; to {verb}, "
                "re-run with confirm=True.",
    }









from vmware_vks.mcp_server._tools_supervisor import register_tools as _register_supervisor

(
    check_vks_compatibility,
    get_supervisor_upgrade_risk,
    get_supervisor_status,
    list_supervisor_storage_policies,
    list_namespaces,
    get_namespace,
    create_namespace,
    update_namespace,
    delete_namespace,
    list_vm_classes,
) = _register_supervisor(mcp, _sys.modules[__name__])


# ---------------------------------------------------------------------------
# TKC tools
# ---------------------------------------------------------------------------















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

from vmware_vks.mcp_server._tools_clusters import register_tools as _register_clusters

(
    list_tkc_clusters,
    get_tkc_cluster,
    get_tkc_available_versions,
    create_tkc_cluster,
    scale_tkc_cluster,
    upgrade_tkc_cluster,
    delete_tkc_cluster,
    issue_supervisor_kubeconfig,
) = _register_clusters(mcp, _sys.modules[__name__])


# destructiveHint is False: this reads the managed cluster and destroys nothing
# in it. It stays [WRITE] and sensitive_result=True, because with `output_path`
# it truncates a file the caller named and the content is a live token — but the
# family's "destructive tools double-confirm in the CLI" gate is about destroying
# managed infrastructure, and demanding two prompts before fetching a kubeconfig
# to a path the operator just typed is friction without a hazard behind it.






# ---------------------------------------------------------------------------
# VM Service tools (vm-operator CRDs; served version discovered at runtime)
# ---------------------------------------------------------------------------








# ---------------------------------------------------------------------------
# Access self-diagnosis (HLD §17 D-17)
# ---------------------------------------------------------------------------



from vmware_vks.mcp_server._tools_access import register_tools as _register_access

(
    issue_tkc_kubeconfig,
    get_harbor_info,
    list_namespace_storage_usage,
    list_vm_service_snapshots,
    list_vm_service_groups,
    list_vm_service_network_interfaces,
    get_access_info,
) = _register_access(mcp, _sys.modules[__name__])


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
