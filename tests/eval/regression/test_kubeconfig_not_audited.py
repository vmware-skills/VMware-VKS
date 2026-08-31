"""A kubeconfig this skill hands out must not end up in ``~/.vmware/audit.db``.

2026-08-30, real-hardware round. ``get_supervisor_kubeconfig``'s own docstring
says the kubeconfig "carries a short-lived session token — treat it as a
credential, do not log or share", and the ``@vmware_tool`` decorator wrapping it
was writing that return value verbatim into the shared audit database. The audit
DB is the artefact most likely to be copied off the machine and attached to a
ticket, so a live Supervisor JWT sitting there in plaintext is worse than an
ordinary log leak.

The fix lives in vmware-policy (``sensitive_result=True`` plus a
credential-key net over every audited result). These tests exercise *this
skill's* two tools end to end against a real SQLite audit DB, because a fix
verified only in the library it lives in is verified in an environment where
this skill's shape cannot fail (CLAUDE.md 形态 #3).

The second finding from the same round is pinned here too: three tools in the
family annotated ``readOnlyHint: true`` write files on the local machine, and
``get_tkc_kubeconfig`` is one of them — ``output_path`` truncates a
caller-chosen file. ``readOnlyHint`` is what an MCP client consults to decide
whether a tool needs confirming, so an annotation that lies is a safety control
that silently does not apply.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
import vmware_policy.audit as audit_mod
import vmware_policy.policy as policy_mod
from vmware_policy.audit import AuditEngine

import vmware_vks.mcp_server.server as srv

TOKEN = "eyJhbGciOiJSUzI1NiJ9.SUPERVISOR_SESSION_JWT_DO_NOT_LOG.sig"

KUBECONFIG = f"""apiVersion: v1
kind: Config
clusters:
- name: supervisor
  cluster: {{server: 'https://10.0.0.5:6443'}}
users:
- name: vsphere-user
  user:
    token: {TOKEN}
"""


@pytest.fixture
def audit_db(tmp_path, monkeypatch):
    """Private audit.db + a stubbed vCenter session, so no hardware is needed."""
    db_path = tmp_path / "audit.db"
    audit_mod._engine = AuditEngine(db_path)
    policy_mod._engine = None
    monkeypatch.setattr(srv, "_get_si", lambda target=None: object())
    yield db_path
    audit_mod._engine = None
    policy_mod._engine = None


def _row_text(db_path) -> str:
    """Every column of every row, concatenated.

    Deliberately not ``SELECT result``: a leak that moves one column over — into
    params, into a traceback, into a column added later — is exactly how this
    survives a test that only inspects the field it expects.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute("SELECT * FROM audit_log")]
    finally:
        conn.close()
    assert rows, "no audit row written — the check would pass vacuously (形态 #1)"
    return "\n".join(f"{k}={v}" for row in rows for k, v in row.items())


@pytest.mark.unit
def test_supervisor_kubeconfig_token_is_not_in_the_audit_db(audit_db, monkeypatch):
    from vmware_vks.ops import kubeconfig as kc

    monkeypatch.setattr(kc, "get_supervisor_kubeconfig_str", lambda si, ns: KUBECONFIG)

    result = srv.get_supervisor_kubeconfig(namespace="ns-prod", target="vc1")

    assert result["kubeconfig"] == KUBECONFIG, "the caller must still get the real thing"
    assert TOKEN not in _row_text(audit_db)


@pytest.mark.unit
def test_tkc_kubeconfig_token_is_not_in_the_audit_db(audit_db, monkeypatch):
    from vmware_vks.ops import kubeconfig as kc

    monkeypatch.setattr(
        kc,
        "write_kubeconfig",
        lambda si, name, ns, output_path=None: {"cluster": name, "kubeconfig": KUBECONFIG},
    )

    result = srv.get_tkc_kubeconfig(name="tkc-1", namespace="ns-prod", target="vc1")

    assert result["kubeconfig"] == KUBECONFIG
    assert TOKEN not in _row_text(audit_db)


@pytest.mark.unit
def test_the_audit_row_still_says_who_called_what_and_whether_it_worked(audit_db, monkeypatch):
    """Control: dropping the whole record would be a different bug, not a fix."""
    import json

    from vmware_vks.ops import kubeconfig as kc

    monkeypatch.setattr(kc, "get_supervisor_kubeconfig_str", lambda si, ns: KUBECONFIG)
    srv.get_supervisor_kubeconfig(namespace="ns-prod", target="vc1")

    conn = sqlite3.connect(str(audit_db))
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute("SELECT * FROM audit_log").fetchone())
    conn.close()

    assert row["tool"] == "get_supervisor_kubeconfig"
    assert row["status"] == "ok"
    assert row["user"]
    assert row["ts"]
    # output_path is recorded as given (None here): where a credential was
    # written is exactly the kind of thing the audit row exists to say.
    assert json.loads(row["params"]) == {
        "namespace": "ns-prod",
        "output_path": None,
        "target": "vc1",
    }


@pytest.mark.unit
def test_an_ordinary_tools_result_is_still_recorded(audit_db, monkeypatch):
    """Control: a decorator that blanked every result would pass the tests above.

    ``get_harbor_info`` is a neighbouring read whose result carries no
    credential — its row must still hold the payload, or the audit trail was
    traded away for the leak fix.
    """
    import json

    from vmware_vks.ops import harbor as hb

    payload = {"registries": [{"id": "r1", "status": "healthy"}], "total": 1}
    monkeypatch.setattr(hb, "get_harbor_info", lambda si: payload)

    srv.get_harbor_info(target="vc1")

    conn = sqlite3.connect(str(audit_db))
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute("SELECT * FROM audit_log").fetchone())
    conn.close()

    assert json.loads(row["result"]) == payload


# ── The annotation that lied ──────────────────────────────────────────


@pytest.mark.unit
def test_both_kubeconfig_tools_declare_their_result_sensitive():
    """The declaration is the contract; the key net is only the backstop."""
    assert srv.get_supervisor_kubeconfig._sensitive_result is True
    assert srv.get_tkc_kubeconfig._sensitive_result is True


@pytest.mark.unit
def test_get_tkc_kubeconfig_is_not_advertised_read_only():
    """It creates directories and truncates a caller-chosen file.

    ``output_path='~/.kube/config'`` overwrites the user's own kubeconfig, so an
    MCP client must be told to confirm rather than auto-run it.
    """
    tools = {t.name: t for t in asyncio.run(srv.mcp.list_tools())}
    assert tools["get_tkc_kubeconfig"].annotations.readOnlyHint is False
    # And the neighbour that genuinely writes nothing locally stays a read.
    assert tools["get_supervisor_kubeconfig"].annotations.readOnlyHint is True


#: Helpers whose whole job is to put bytes on this machine's disk.
_FS_WRITERS = frozenset(
    {"_write_kubeconfig_file", "write_kubeconfig", "write_text", "write_bytes"}
)


def _fs_writers_called_by(tool_name: str) -> list[str]:
    """Names from :data:`_FS_WRITERS` called in ``tool_name``'s own body."""
    import ast
    import inspect as _inspect

    fn = _inspect.unwrap(getattr(srv, tool_name))
    tree = ast.parse(_inspect.getsource(fn))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if called in _FS_WRITERS:
            found.append(called)
    return found


@pytest.mark.unit
def test_the_filesystem_writer_scan_actually_detects_one():
    """Positive control for the scan below.

    A scan that finds nothing anywhere proves nothing (形态 #1). Pin it against
    the one tool known to write: if this stops detecting ``write_kubeconfig``,
    the scan below has quietly become a check that can never fail.
    """
    assert _fs_writers_called_by("get_tkc_kubeconfig") == ["write_kubeconfig"]


@pytest.mark.unit
def test_no_read_only_tool_calls_a_filesystem_writer_directly():
    """The open question behind the finding, asked of every tool (形态 #2).

    Scope, stated so the name does not promise more than it checks (形态 #4):
    this reads each read-only tool's **own body** for a direct call to one of
    :data:`_FS_WRITERS`. It does not follow the call graph, so it catches the
    shape this finding had — the tool itself handing a caller-chosen path to a
    writer — and not a write buried three modules down.
    """
    tools = asyncio.run(srv.mcp.list_tools())
    read_only = [t.name for t in tools if t.annotations and t.annotations.readOnlyHint]
    assert read_only, "no read-only tools found — the check would be vacuous"

    offenders = {
        name: writers for name in read_only if (writers := _fs_writers_called_by(name))
    }
    assert not offenders, (
        f"annotated readOnlyHint=true but write local files: {offenders}. "
        f"readOnlyHint is what an MCP client uses to decide whether to confirm."
    )
