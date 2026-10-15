"""Tests for vmware_vks.connection (unit, no real vCenter)."""
import socket
import ssl
from unittest.mock import MagicMock, patch

import pytest

from vmware_vks import connection as conn_mod
from vmware_vks.config import AppConfig, ConfigError, TargetConfig
from vmware_vks.connection import ConnectionManager
from vmware_vks.mcp_server.server import _safe_error


def _make_config():
    target = TargetConfig(name="vc1", host="vc.example.com", config_username="admin@vsphere.local")
    return AppConfig(targets=(target,))


def test_connection_manager_list_targets():
    mgr = ConnectionManager(_make_config())
    assert mgr.list_targets() == ["vc1"]


def test_connection_manager_connect_reuses_session(monkeypatch):
    monkeypatch.setenv("VMWARE_VKS_VC1_PASSWORD", "pw")
    mgr = ConnectionManager(_make_config())

    mock_si = MagicMock()
    mock_si.content.sessionManager.currentSession = "active"

    with patch.object(mgr, "_create_connection", return_value=mock_si) as mock_create:
        si1 = mgr.connect("vc1")
        si2 = mgr.connect("vc1")
        assert si1 is si2
        mock_create.assert_called_once()


def test_connect_eviction_pops_id_si_side_stores(monkeypatch):
    """Evicting a stale session must drop its id(si)-keyed metadata immediately,
    not wait for atexit — otherwise GC + id() reuse leaks stale verify_ssl/target
    onto a new si for a different target (id-reuse hazard, code-review LOW)."""
    monkeypatch.setenv("VMWARE_VKS_VC1_PASSWORD", "pw")
    target = _make_config().targets[0]
    mgr = ConnectionManager(_make_config())

    stale_si = MagicMock()
    stale_si.content.sessionManager.currentSession = "active"
    fresh_si = MagicMock()
    fresh_si.content.sessionManager.currentSession = "active"

    # Seed the connection cache + side stores as a live connect() would.
    with patch.object(mgr, "_create_connection", return_value=stale_si):
        assert mgr.connect("vc1") is stale_si
    key = id(stale_si)
    conn_mod._SI_VERIFY_SSL[key] = target.verify_ssl
    conn_mod._SI_TARGET[key] = target
    # Simulate a dead session: currentSession access raises -> triggers eviction.
    type(stale_si).content = property(
        lambda self: (_ for _ in ()).throw(Exception("session dead"))
    )

    with patch.object(mgr, "_create_connection", return_value=fresh_si), \
         patch("pyVim.connect.Disconnect"):
        result = mgr.connect("vc1")

    assert result is fresh_si
    # The stale si's side-store entries must be gone right now.
    assert key not in conn_mod._SI_VERIFY_SSL
    assert key not in conn_mod._SI_TARGET


# ---------------------------------------------------------------------------
# SmartConnect failures are translated, not passed through raw
# ---------------------------------------------------------------------------
#
# A pyVmomi connection failure arrives as a raw OSError subclass whose text is
# the most sensitive in this skill: the certificate subject, the hostname that
# would not resolve, the host:port that refused. _safe_error withholds those, so
# without translation an agent is told only the class name. Each branch names
# the config target and the setting to change instead, and the original stays on
# __cause__ for the server log.


def _connect_failing_with(exc, monkeypatch):
    """Drive _create_connection with SmartConnect raising ``exc``."""
    monkeypatch.setenv("VMWARE_VKS_VC1_PASSWORD", "pw")
    target = _make_config().targets[0]
    fake = MagicMock(side_effect=exc)
    monkeypatch.setitem(
        __import__("sys").modules,
        "pyVim.connect",
        MagicMock(SmartConnect=fake, Disconnect=MagicMock()),
    )
    return target


def test_tls_failure_names_the_target_and_the_setting(monkeypatch):
    """The remedy for a self-signed cert is a config edit, so say which one."""
    tls = ssl.SSLCertVerificationError(
        1,
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Hostname "
        "mismatch, certificate is not valid for 'vc-prod.internal'",
    )
    target = _connect_failing_with(tls, monkeypatch)

    with pytest.raises(ConfigError) as exc_info:
        ConnectionManager._create_connection(target)

    msg = str(exc_info.value)
    assert "vc1" in msg and "verify_ssl: false" in msg
    assert "vc-prod.internal" not in msg
    assert "CERTIFICATE_VERIFY_FAILED" not in msg
    assert exc_info.value.__cause__ is tls, "raw detail must survive for the log"

    # And it survives the wrapper an agent reads through.
    assert _safe_error(exc_info.value, "t") == msg


def test_dns_failure_points_at_the_host_value(monkeypatch):
    dns = socket.gaierror(-2, "Name or service not known: vc-typo.internal")
    target = _connect_failing_with(dns, monkeypatch)

    with pytest.raises(ConfigError) as exc_info:
        ConnectionManager._create_connection(target)

    msg = str(exc_info.value)
    assert "vc1" in msg and "'host'" in msg
    assert "vc-typo.internal" not in msg


def test_unreachable_host_is_a_connection_error(monkeypatch):
    """The authored unreachable message carries its own type.

    It used to be a builtin ``ConnectionError``, which forced that type onto the
    passthrough allowlist so the text could reach the agent — and the same entry
    then passed urllib3's own ``ConnectionError``, whose message is
    ``HTTPSConnectionPool(host='vc.internal', port=443)``. One type from two
    sources, which an allowlist cannot separate; ``ConfigError`` is what the two
    sibling branches (TLS, DNS) already raise.
    """
    refused = ConnectionRefusedError(61, "Connection refused")
    target = _connect_failing_with(refused, monkeypatch)

    with pytest.raises(ConfigError) as exc_info:
        ConnectionManager._create_connection(target)

    msg = str(exc_info.value)
    assert "vc1" in msg and "reach" in msg
    # Raised deliberately, so it has to reach the agent rather than be reduced.
    assert _safe_error(exc_info.value, "t") == msg


def test_missing_password_is_not_relabelled_as_a_connection_failure(monkeypatch):
    """The credential read must happen before the try, or this breaks.

    ``ConfigError`` is an ``OSError`` subclass, so a naive wrap catches the
    missing-password error in the ``except OSError`` branch and answers this
    family's most common first-run failure with "check that the host is up".
    """
    monkeypatch.delenv("VMWARE_VKS_VC1_PASSWORD", raising=False)
    target = _make_config().targets[0]
    smart = MagicMock(side_effect=AssertionError("SmartConnect must not be reached"))
    monkeypatch.setitem(
        __import__("sys").modules,
        "pyVim.connect",
        MagicMock(SmartConnect=smart, Disconnect=MagicMock()),
    )

    with pytest.raises(ConfigError) as exc_info:
        ConnectionManager._create_connection(target)

    msg = str(exc_info.value)
    assert "VMWARE_VKS_VC1_PASSWORD" in msg, (
        "the env var name is the whole remedy and must not be replaced by a "
        "connection remedy"
    )
    assert "verify_ssl" not in msg and "host is up" not in msg
    smart.assert_not_called()
