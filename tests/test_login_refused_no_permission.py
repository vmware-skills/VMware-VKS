"""A login refused with NoPermission gets authored text, not the raw fault.

Lab vCenter 8.0.3, 2026-10-18: an SSO account with no permission anywhere is
refused at Login with vim.fault.NoPermission (System.View on the root folder).
VKS never claims "no vSphere role" (a namespace-only account is a real VKS
user), but it must not hand the raw fault to an agent either.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pyVmomi import vim

from vmware_vks.config import AppConfig, ConfigError, TargetConfig
from vmware_vks.connection import ConnectionManager

pytestmark = pytest.mark.unit


def _mgr(monkeypatch: pytest.MonkeyPatch) -> ConnectionManager:
    monkeypatch.setenv("VMWARE_VKS_LAB_PASSWORD", "pw")
    monkeypatch.setenv("VMWARE_LAB_PASSWORD", "pw")
    return ConnectionManager(
        AppConfig(targets=(TargetConfig(name="lab", host="vc.example.com", config_username="u@vsphere.local"),))
    )


def test_no_permission_at_login_is_authored(monkeypatch: pytest.MonkeyPatch) -> None:
    fault = vim.fault.NoPermission(privilegeId="System.View")
    with patch("pyVim.connect.SmartConnect", side_effect=fault):
        with pytest.raises(ConfigError) as info:
            _mgr(monkeypatch).connect("lab")
    msg = str(info.value)
    assert "'lab'" in msg and "NoPermission" in msg and "vmware-vks doctor" in msg
    assert "dynamicType" not in msg and "no vSphere role" not in msg
    assert info.value.__cause__ is fault


def test_wrong_password_still_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    with patch("pyVim.connect.SmartConnect", side_effect=vim.fault.InvalidLogin()):
        with pytest.raises(vim.fault.InvalidLogin):
            _mgr(monkeypatch).connect("lab")
