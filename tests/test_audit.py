"""Tests for AuditLogger, and for what the central audit records on failure."""
import json
from unittest.mock import patch
import pytest
from vmware_vks.notify.audit import AuditLogger


def test_audit_log_writes_json_line(tmp_path):
    log_file = tmp_path / "audit.log"
    logger = AuditLogger(log_file=log_file)

    logger.log(
        target="vcenter.example.com",
        operation="create_namespace",
        resource="dev",
        parameters={"cluster_id": "domain-c1"},
        result="success",
        user="admin@vsphere.local",
    )

    content = log_file.read_text(encoding="utf-8").strip()
    entry = json.loads(content)
    assert entry["target"] == "vcenter.example.com"
    assert entry["operation"] == "create_namespace"
    assert entry["resource"] == "dev"
    assert entry["parameters"] == {"cluster_id": "domain-c1"}
    assert entry["result"] == "success"
    assert entry["user"] == "admin@vsphere.local"
    assert "timestamp" in entry


def test_audit_log_creates_directory(tmp_path):
    nested_dir = tmp_path / "sub" / "dir"
    log_file = nested_dir / "audit.log"

    # Directory should not exist yet
    assert not nested_dir.exists()

    logger = AuditLogger(log_file=log_file)

    # Directory should be created by __init__
    assert nested_dir.exists()

    logger.log(
        target="vcenter.example.com",
        operation="test",
        resource="test-res",
        parameters={},
        result="ok",
    )
    assert log_file.exists()


def test_audit_log_appends(tmp_path):
    log_file = tmp_path / "audit.log"
    logger = AuditLogger(log_file=log_file)

    logger.log(
        target="vc1",
        operation="op1",
        resource="res1",
        parameters={},
        result="success",
    )
    logger.log(
        target="vc2",
        operation="op2",
        resource="res2",
        parameters={"key": "val"},
        result="failure",
    )

    lines = log_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2

    entry1 = json.loads(lines[0])
    entry2 = json.loads(lines[1])
    assert entry1["target"] == "vc1"
    assert entry2["target"] == "vc2"
    assert entry2["result"] == "failure"


def test_returned_failure_is_audited_as_a_failure(monkeypatch):
    """A tool that catches and returns must not be recorded as a success.

    ``@vmware_tool`` marks a call failed when an exception reaches it, or when
    the returned payload is the family's error envelope. Every tool in this
    skill catches and returns ``{"error": ..., "hint": ...}``, which is that
    envelope — so the audit row, the undo token and the circuit breaker all see
    a failure without the skill having to report one. That is only true while
    the payload stays dict-shaped: a tool that returned an error *string*
    instead would be indistinguishable from a success, and would need an
    explicit ``vmware_policy.report_tool_failure`` call.
    """
    from vmware_vks.mcp_server import server as srv

    rows = []

    class _Recorder:
        def log(self, **kw):
            rows.append(kw)

    monkeypatch.setattr("vmware_policy.guard.get_engine", lambda: _Recorder())
    monkeypatch.setattr(
        srv, "_get_si", lambda target=None: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    result = srv.list_namespaces()

    assert rows, "the call was never audited at all"
    assert rows[0]["status"] == "error", (
        f"a failed call was audited as {rows[0]['status']!r} — the undo token and "
        "the circuit breaker read the same flag. A tool whose failure payload is "
        "not the dict envelope must call vmware_policy.report_tool_failure."
    )
    assert isinstance(result, dict) and result["error"], (
        "the failure payload must stay dict-shaped — that is what @vmware_tool "
        "detects, and it is why this skill needs no report_tool_failure call"
    )
