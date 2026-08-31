"""Tests for vmware_vks.config."""
import os
from pathlib import Path
import pytest
from vmware_vks.config import AppConfig, TargetConfig, load_config


def test_target_config_password_from_env(monkeypatch):
    monkeypatch.setenv("VMWARE_VKS_VCENTER01_PASSWORD", "secret123")
    t = TargetConfig(name="vcenter01", host="vc.example.com", config_username="admin@vsphere.local")
    assert t.password == "secret123"


def test_target_config_password_missing_raises(monkeypatch):
    monkeypatch.delenv("VMWARE_VKS_VCENTER01_PASSWORD", raising=False)
    t = TargetConfig(name="vcenter01", host="vc.example.com", config_username="admin@vsphere.local")
    with pytest.raises(OSError, match="VMWARE_VKS_VCENTER01_PASSWORD"):
        _ = t.password


def test_load_config(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "targets:\n"
        "  - name: vc1\n"
        "    host: vc.example.com\n"
        "    username: admin@vsphere.local\n"
    , encoding="utf-8")
    config = load_config(cfg_file)
    assert len(config.targets) == 1
    assert config.targets[0].name == "vc1"
    assert config.default_target.host == "vc.example.com"


def test_load_config_missing_file():
    with pytest.raises(FileNotFoundError):
        load_config(Path("/nonexistent/config.yaml"))


def test_get_target_not_found(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "targets:\n"
        "  - name: vc1\n"
        "    host: vc.example.com\n"
        "    username: admin@vsphere.local\n"
    , encoding="utf-8")
    config = load_config(cfg_file)
    with pytest.raises(KeyError, match="vc2"):
        config.get_target("vc2")


def test_username_and_password_rotate_together(tmp_path, monkeypatch):
    """Both halves of a credential must resolve at the same moment.

    The env override exists so a secret store can supply the pair. Reading the
    username once at load time while the password stays a property splits it:
    a sidecar rotating both mid-process moves the password and leaves the
    username behind, and the login uses a combination that was never issued
    together. That is the failure this override was added to prevent, so it
    must not be reintroduced by the fix for it.
    """
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "targets:\n"
        "  - name: vc1\n"
        "    host: vc.example.com\n"
        "    username: config-file-user\n"
    , encoding="utf-8")

    monkeypatch.setenv("VMWARE_VKS_VC1_USERNAME", "svc-a@vsphere.local")
    monkeypatch.setenv("VMWARE_VKS_VC1_PASSWORD", "pw-a")
    target = load_config(cfg_file).targets[0]
    assert (target.username, target.password) == ("svc-a@vsphere.local", "pw-a")

    monkeypatch.setenv("VMWARE_VKS_VC1_USERNAME", "svc-b@vsphere.local")
    monkeypatch.setenv("VMWARE_VKS_VC1_PASSWORD", "pw-b")
    assert (target.username, target.password) == ("svc-b@vsphere.local", "pw-b"), (
        "the pair came apart — one half is bound at load time and the other at access"
    )


def test_username_falls_back_to_config_file(tmp_path, monkeypatch):
    """With no env var set, the config.yaml value is what gets used."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "targets:\n"
        "  - name: vc1\n"
        "    host: vc.example.com\n"
        "    username: config-file-user\n"
    , encoding="utf-8")
    monkeypatch.delenv("VMWARE_VKS_VC1_USERNAME", raising=False)
    target = load_config(cfg_file).targets[0]
    assert target.config_username == "config-file-user"
    assert target.username == "config-file-user"
