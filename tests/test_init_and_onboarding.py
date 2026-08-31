"""Regression tests for onboarding: the `vmware-vks init` wizard, the doctor
init reference (no false promise — 踩坑 #2), and teaching auth/TLS errors.
"""

from __future__ import annotations

from vmware_policy.fsperms import assert_owner_only

import ssl
from pathlib import Path

import pytest
import typer

from vmware_vks import init_wizard


# ── init wizard ──────────────────────────────────────────────────────────────


@pytest.fixture
def _wizard_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg_dir = tmp_path / ".vmware-vks"
    monkeypatch.setattr(init_wizard, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(init_wizard, "CONFIG_FILE", cfg_dir / "config.yaml")
    monkeypatch.setattr(init_wizard, "ENV_FILE", cfg_dir / ".env")
    return cfg_dir


def _feed(
    monkeypatch: pytest.MonkeyPatch, answers: list[object], confirms: list[bool]
) -> None:
    a = iter(answers)
    c = iter(confirms)
    monkeypatch.setattr(init_wizard.typer, "prompt", lambda *args, **kwargs: next(a))
    monkeypatch.setattr(init_wizard.typer, "confirm", lambda *args, **kwargs: next(c))


def test_init_writes_grep_safe_env(
    _wizard_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vmware_vks.config import _decode_secret

    _feed(
        monkeypatch,
        answers=["lab-vc", "10.1.2.3", "administrator@vsphere.local", 443, "S3cr3t!pw"],
        confirms=[True],
    )
    assert init_wizard.run_init(skip_test=True) == 0

    env_text = (_wizard_env / ".env").read_text(encoding="utf-8")
    # Env key must match config.py's TargetConfig.password: VMWARE_VKS_<NAME>_PASSWORD.
    assert "VMWARE_VKS_LAB_VC_PASSWORD=b64:" in env_text
    assert "S3cr3t!pw" not in env_text  # never plaintext on disk
    assert_owner_only(_wizard_env / ".env")
    line = next(
        ln
        for ln in env_text.splitlines()
        if ln.startswith("VMWARE_VKS_LAB_VC_PASSWORD=")
    )
    assert _decode_secret(line.split("=", 1)[1]) == "S3cr3t!pw"


# ── doctor references a real init command (no false promise) ──────────────────


def _init_registered() -> bool:
    from vmware_vks.cli import app

    return any(c.name == "init" for c in app.registered_commands)


def test_doctor_init_reference_is_backed_by_real_command():
    from vmware_vks import doctor

    src = Path(doctor.__file__).read_text(encoding="utf-8")
    if "vmware-vks init" in src:
        assert _init_registered(), (
            "doctor recommends init but no such command is registered"
        )


# ── auth errors teach where to fix the problem ───────────────────────────────


def test_invalid_login_error_is_teaching(capsys):
    from pyVmomi import vim

    from vmware_vks.cli import _cli_errors

    @_cli_errors
    def boom():
        raise vim.fault.InvalidLogin()

    with pytest.raises(typer.Exit):
        boom()
    out = capsys.readouterr().out
    assert ".vmware-vks/.env" in out
    assert "VMWARE_VKS_<TARGET>_PASSWORD" in out


def test_wcp_auth_denied_error_is_teaching(capsys):
    """Supervisor /wcp/login (WCP-REST) 401/403 also points at the .env."""
    from vmware_vks.cli import _cli_errors
    from vmware_vks.errors import VksApiError

    @_cli_errors
    def boom():
        raise VksApiError("Supervisor /wcp/login denied access", status_code=401)

    with pytest.raises(typer.Exit):
        boom()
    out = capsys.readouterr().out
    assert ".vmware-vks/.env" in out
    assert "VMWARE_VKS_<TARGET>_PASSWORD" in out


def test_tls_error_is_teaching(capsys):
    from vmware_vks.cli import _cli_errors

    @_cli_errors
    def boom():
        raise ssl.SSLError("certificate verify failed")

    with pytest.raises(typer.Exit):
        boom()
    out = capsys.readouterr().out
    assert "verify_ssl" in out
