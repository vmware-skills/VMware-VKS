"""A login that works but has no vSphere role (AIops#47).

Reported on vCenter 8.0.3: an account with no vSphere role logs in, vCenter
shows no session for it (``currentSession`` is ``None``), and every inventory
call then fails with ``vim.fault.NotAuthenticated``.

VKS builds its vSphere Automation REST session and its Supervisor /wcp/login
token on top of the SOAP session (both take ``si``), so refusing at connect
time is what keeps a role-less account from reaching either with a message
about something else. Driven through the real ConnectionManager, doctor,
preflight, CLI handler and MCP tools; only SmartConnect/Disconnect are fakes.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest
import typer
from pyVmomi import vim
from rich.console import Console

from vmware_vks import doctor
from vmware_vks.config import AppConfig, TargetConfig
from vmware_vks.connection import _SI_ATEXIT, ConnectionManager, NoVsphereRoleError
from vmware_vks.mcp_server import server

ROLE_WORDS = ("no vSphere role", "Read-Only")


def _config() -> AppConfig:
    return AppConfig(
        targets=(
            TargetConfig(name="lab", host="vc.example.com", config_username="u@vsphere.local"),
        )
    )


@pytest.fixture
def role_less(monkeypatch):
    """SmartConnect logs in, but vCenter shows no session for the account."""
    monkeypatch.setenv("VMWARE_VKS_LAB_PASSWORD", "pw")
    si = MagicMock(name="si")
    si.content.sessionManager.currentSession = None
    with (
        patch("pyVim.connect.SmartConnect", return_value=si) as smart,
        patch("pyVim.connect.Disconnect") as dis,
    ):
        yield si, smart, dis


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_role_less_login_is_refused_closed_and_not_cached(role_less):
    si, smart, dis = role_less
    mgr = ConnectionManager(_config())
    with pytest.raises(NoVsphereRoleError) as excinfo:
        mgr.connect("lab")
    for word in ROLE_WORDS:
        assert word in str(excinfo.value)
    dis.assert_called_once_with(si)
    assert id(si) not in _SI_ATEXIT
    assert mgr.list_connected() == []
    with pytest.raises(NoVsphereRoleError):
        mgr.connect("lab")
    assert smart.call_count == 2


def test_normal_login_is_unaffected(monkeypatch):
    monkeypatch.setenv("VMWARE_VKS_LAB_PASSWORD", "pw")
    si = MagicMock(name="si")
    si.content.sessionManager.currentSession = MagicMock()
    mgr = ConnectionManager(_config())
    with patch("pyVim.connect.SmartConnect", return_value=si), patch(
        "pyVim.connect.Disconnect"
    ) as dis:
        assert mgr.connect("lab") is si
        assert mgr.list_connected() == ["lab"]
        dis.assert_not_called()
        mgr.disconnect_all()


def test_rest_tool_names_the_role_and_never_logs_in_to_rest(role_less, monkeypatch):
    """list_namespaces goes through POST /api/session; a role-less account must
    get the role message, not a REST 401 hint that says 'this is not a role'."""
    mgr = ConnectionManager(_config())
    monkeypatch.setattr(server, "_get_conn_mgr", lambda: mgr)
    with patch("vmware_vks.rest_session.login") as rest_login, patch(
        "vmware_vks.wcp_login.wcp_login"
    ) as wcp:
        out = server.list_namespaces(target="lab")
    rest_login.assert_not_called()
    wcp.assert_not_called()
    for word in ROLE_WORDS:
        assert word in out["error"]
    assert "vmware-vks doctor" in out["error"]  # the remedy at the end survives the cap
    assert "credentials" not in out.get("hint", "")


def test_preflight_names_the_role_without_a_credentials_hint(role_less, monkeypatch):
    from vmware_vks import preflight_auth

    monkeypatch.setattr("vmware_vks.config.load_config", lambda *a, **k: _config())
    result = preflight_auth.run_preflight_auth("lab")
    assert len(result.steps) == 1 and not result.steps[0].ok
    detail = result.steps[0].detail
    for word in ROLE_WORDS:
        assert word in detail
    assert "credentials" not in detail


def test_doctor_row_names_the_missing_role(role_less, monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("targets: []\n")
    monkeypatch.setattr("vmware_vks.config.load_config", lambda *a, **k: _config())
    buf = io.StringIO()
    monkeypatch.setattr(doctor, "console", Console(file=buf, width=1000))
    assert doctor.run_doctor(cfg_file) is False
    row = next(line for line in buf.getvalue().splitlines() if "vCenter reachable (lab)" in line)
    assert "FAIL" in row
    for word in ROLE_WORDS:
        assert word in row


def test_cli_handler_prints_the_role_message(role_less, monkeypatch, capsys):
    from vmware_vks import cli

    monkeypatch.setattr("vmware_vks.config.load_config", lambda *a, **k: _config())

    @cli._cli_errors
    def run():
        cli._get_si("lab")

    with pytest.raises(typer.Exit):
        run()
    out = _flat(capsys.readouterr().out)
    for word in ROLE_WORDS:
        assert word in out


# ── NotAuthenticated raised later: expired session, or a role removed ─────────


def _says_both(text: str) -> None:
    text = _flat(text)
    assert "expired" in text and "run the command again" in text
    assert "no vSphere role" in text and "Read-Only" in text


def test_cli_not_authenticated_says_both_causes(capsys):
    from vmware_vks.cli import _cli_errors

    @_cli_errors
    def boom():
        raise vim.fault.NotAuthenticated()

    with pytest.raises(typer.Exit):
        boom()
    _says_both(capsys.readouterr().out)


def test_mcp_not_authenticated_says_both_causes(monkeypatch):
    def _boom(target=None):
        raise vim.fault.NotAuthenticated()

    monkeypatch.setattr(server, "_get_si", _boom)
    out = server.list_namespaces(target="lab")
    _says_both(out["error"])
    assert "credentials" not in out.get("hint", "")
