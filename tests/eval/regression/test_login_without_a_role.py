"""A login that works but shows no session, and the role hint that follows (AIops#47).

Reported on vCenter 8.0.3: an account with no vSphere role logs in, vCenter
shows no session for it (``currentSession`` is ``None``), and every inventory
call then fails with ``vim.fault.NotAuthenticated``.

The other skills refuse such a login at connect time. VKS does not: its users
may hold only Supervisor namespace permissions, which no vSphere-role check can
judge, and a refusal would turn a working setup into a hard failure. So the
login connects, and the NotAuthenticated that follows, if any, names both
causes (expired session, or no role). Driven through the real
ConnectionManager, CLI handler and MCP tools; only SmartConnect/Disconnect are
fakes.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import typer
from pyVmomi import vim

from vmware_vks import connection as conn_mod
from vmware_vks.config import AppConfig, TargetConfig
from vmware_vks.connection import ConnectionManager
from vmware_vks.mcp_server import server


def _config() -> AppConfig:
    return AppConfig(
        targets=(
            TargetConfig(name="lab", host="vc.example.com", config_username="u@vsphere.local"),
        )
    )


@pytest.fixture
def no_session(monkeypatch):
    """SmartConnect logs in, vCenter shows no session, and the root view is refused.

    Both signals vmware-policy reads as "no role": if VKS still asked, this is
    the login it would refuse.
    """
    monkeypatch.setenv("VMWARE_VKS_LAB_PASSWORD", "pw")
    si = MagicMock(name="si")
    si.content.sessionManager.currentSession = None
    si.content.viewManager.CreateContainerView.side_effect = vim.fault.NotAuthenticated()
    with (
        patch("pyVim.connect.SmartConnect", return_value=si) as smart,
        patch("pyVim.connect.Disconnect") as dis,
    ):
        yield si, smart, dis


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_a_login_with_no_session_connects(no_session):
    """Not refused: namespace-only accounts look like this and still work."""
    si, smart, dis = no_session
    mgr = ConnectionManager(_config())
    assert mgr.connect("lab") is si  # no error
    dis.assert_not_called()  # not logged out
    assert smart.call_count == 1
    assert id(si) in conn_mod._SI_ATEXIT  # registered like any live login
    mgr.disconnect_all()


def test_no_connect_time_role_probe_is_made(no_session):
    """The probe itself is gone, not merely its verdict ignored."""
    si, _smart, _dis = no_session
    ConnectionManager._create_connection(_config().get_target("lab"))
    si.content.viewManager.CreateContainerView.assert_not_called()
    conn_mod._close_si(si)


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


def test_cli_get_si_returns_the_session(no_session, monkeypatch):
    from vmware_vks import cli

    si, _smart, _dis = no_session
    monkeypatch.setattr("vmware_vks.config.load_config", lambda *a, **k: _config())

    @cli._cli_errors
    def run():
        return cli._get_si("lab")

    assert run() is si


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
