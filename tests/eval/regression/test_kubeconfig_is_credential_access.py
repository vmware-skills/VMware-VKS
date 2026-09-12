"""Kubeconfig retrieval is credential access, not a read.

2026-09-11, ClawHub security review of the published skill. Both kubeconfig
tools hand back a live Supervisor bearer token (the JWT from ``/wcp/login``),
yet ``get_supervisor_kubeconfig`` — the higher-privileged of the two — was
annotated ``readOnlyHint: true`` and listed as "L1, always auto-run" in the
docs. ``readOnlyHint`` is what an MCP client consults to decide whether to run a
tool without asking, so the one tool that most needed a human in the loop was
the one advertised as not needing one.

The annotation was also stale on its own terms: the TKC sibling is
``readOnlyHint: false`` because ``output_path`` truncates a caller-chosen file,
and the Supervisor tool gained the same ``output_path`` later without the
annotation following. A regression test pinned the stale value ("the neighbour
that genuinely writes nothing locally stays a read"), and the scan meant to
catch read-only tools that write files did not list the writer this tool calls.

Pinned here:

* both tools are not auto-runnable, declared sensitive, and at the same policy
  risk level on the MCP and CLI surfaces;
* the CLI can write the Supervisor kubeconfig to a file (it previously could
  only print it) and prints to stdout without mangling the token;
* an exported kubeconfig is owner-only even when it replaces an existing file
  with a wider mode — and is restricted *before* the token is written.
"""

from __future__ import annotations

import asyncio
import os
import stat
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner
from vmware_policy.fsperms import POSIX_PERMISSIONS, assert_owner_only

import vmware_vks.mcp_server.server as srv
import vmware_vks.ops.kubeconfig as kc
from vmware_vks import cli

# Long enough that Rich would fold it at an 80-column console.
TOKEN = "eyJhbGciOiJSUzI1NiJ9." + "A" * 400 + ".sig"
KUBECONFIG = (
    "apiVersion: v1\nkind: Config\nusers:\n- name: vsphere-user\n"
    f"  user:\n    token: {TOKEN}\n"
)

KUBECONFIG_TOOLS = ("get_supervisor_kubeconfig", "get_tkc_kubeconfig")


@pytest.fixture
def fake_backend(monkeypatch):
    """No vCenter: the CLI gets a mock SI and the ops layer a fixed kubeconfig."""
    monkeypatch.setattr(cli, "_get_si", lambda target=None: MagicMock())
    monkeypatch.setattr(kc, "get_supervisor_kubeconfig_str", lambda si, ns: KUBECONFIG)
    monkeypatch.setattr(kc, "get_tkc_kubeconfig_str", lambda si, name, ns: KUBECONFIG)


# ── Classification ────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("tool", KUBECONFIG_TOOLS)
def test_kubeconfig_tools_are_not_advertised_as_auto_runnable(tool):
    tools = {t.name: t for t in asyncio.run(srv.mcp.list_tools())}
    t = tools[tool]
    assert t.annotations.readOnlyHint is False, (
        f"{tool} returns a live bearer token; readOnlyHint=true lets an MCP "
        f"client run it without asking"
    )
    # It destroys nothing in the managed cluster.
    assert t.annotations.destructiveHint is False
    desc = (t.description or "").lstrip()
    assert desc.startswith("[WRITE] Credential access"), desc[:80]
    assert "only when the user explicitly asks" in " ".join(desc.split())


@pytest.mark.unit
@pytest.mark.parametrize("tool", KUBECONFIG_TOOLS)
def test_kubeconfig_tools_are_sensitive_and_not_low_risk(tool):
    fn = getattr(srv, tool)
    assert fn._sensitive_result is True
    assert fn._risk_level == "medium", (
        f"{tool} is credential access; 'low' puts it outside every "
        f"min_risk_level deny rule an operator can write"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("command", "tool"),
    [("kubeconfig_supervisor", "get_supervisor_kubeconfig"),
     ("kubeconfig_get", "get_tkc_kubeconfig")],
)
def test_cli_kubeconfig_commands_are_guarded_at_the_mcp_risk_level(command, tool):
    """One deny rule must scope both surfaces (HLD I-3).

    Deny rules match on the tool *name*, so the risk level alone is not enough:
    ``kubeconfig get`` was guarded as ``kubeconfig_get`` (the default, the
    function's ``__name__``), and a rule denying ``get_tkc_kubeconfig`` stopped
    the MCP tool while the CLI handed the same token out.
    """
    fn = getattr(cli, command)
    assert getattr(fn, "_is_guarded", False), f"{command} bypasses policy + audit"
    assert fn._guarded_tool == tool, (
        f"{command} is guarded as {fn._guarded_tool!r}; a deny rule on {tool!r} "
        f"does not reach it"
    )
    assert fn._risk_level == getattr(srv, tool)._risk_level


# ── CLI output ────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "argv",
    [["kubeconfig", "supervisor", "-n", "ns1"],
     ["kubeconfig", "get", "c1", "-n", "ns1"]],
)
def test_cli_stdout_kubeconfig_is_byte_for_byte(fake_backend, argv):
    """Rich folded the token at 80 columns, so a redirected kubeconfig was broken."""
    result = CliRunner().invoke(cli.app, argv)
    assert result.exit_code == 0, result.output
    assert result.output == KUBECONFIG


@pytest.mark.unit
@pytest.mark.parametrize(
    "argv",
    [["kubeconfig", "supervisor", "-n", "ns1"],
     ["kubeconfig", "get", "c1", "-n", "ns1"]],
)
def test_cli_output_flag_writes_owner_only_and_prints_no_token(fake_backend, tmp_path, argv):
    out = tmp_path / "sub" / "kc.yaml"
    result = CliRunner().invoke(cli.app, [*argv, "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert TOKEN not in result.output
    assert out.read_text(encoding="utf-8") == KUBECONFIG
    assert_owner_only(out)


# ── The file on disk ──────────────────────────────────────────────────


@pytest.mark.unit
def test_supervisor_export_is_owner_only(fake_backend, tmp_path):
    out = tmp_path / "supervisor.yaml"
    result = kc.write_supervisor_kubeconfig(MagicMock(), "ns1", output_path=out)
    assert result == {"namespace": "ns1", "written_to": str(out)}
    assert_owner_only(out)


@pytest.mark.unit
def test_reexport_over_a_wider_file_ends_owner_only(tmp_path):
    out = tmp_path / "kc.yaml"
    out.write_text("old\n", encoding="utf-8")
    os.chmod(out, 0o644)
    kc._write_kubeconfig_file(out, KUBECONFIG)
    assert out.read_text(encoding="utf-8") == KUBECONFIG
    assert_owner_only(out)


@pytest.mark.unit
@pytest.mark.skipif(not POSIX_PERMISSIONS, reason="mode bits are not meaningful here")
def test_existing_file_is_restricted_before_the_token_is_written(tmp_path, monkeypatch):
    """O_CREAT's 0o600 does not apply to a file that already exists.

    Before the fix the mode was fixed with a chmod *after* the write, so a
    0644 file held the live token for the length of the write.
    """
    out = tmp_path / "kc.yaml"
    out.write_text("old\n", encoding="utf-8")
    os.chmod(out, 0o644)

    real_fdopen = os.fdopen
    modes_at_write: list[int] = []

    def spy(fd, *args, **kwargs):
        modes_at_write.append(stat.S_IMODE(os.fstat(fd).st_mode))
        return real_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(kc.os, "fdopen", spy)
    kc._write_kubeconfig_file(out, KUBECONFIG)
    assert modes_at_write == [0o600], (
        f"file mode was {[oct(m) for m in modes_at_write]} when the token was written"
    )


@pytest.mark.unit
@pytest.mark.skipif(not hasattr(os, "fchmod"), reason="no fchmod on this platform")
def test_refuses_to_write_a_token_it_cannot_make_owner_only(tmp_path, monkeypatch):
    out = tmp_path / "kc.yaml"

    def deny(fd, mode):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(kc.os, "fchmod", deny)
    with pytest.raises(ValueError, match="owner-only"):
        kc._write_kubeconfig_file(out, KUBECONFIG)
    assert not out.exists()
    assert list(tmp_path.iterdir()) == [], "temp file left behind"


@pytest.mark.unit
@pytest.mark.skipif(
    os.name == "nt", reason="Windows refuses to replace a file another handle holds open"
)
def test_a_descriptor_open_on_the_old_file_never_sees_the_new_token(tmp_path):
    """fchmod does not revoke descriptors that are already open.

    The writer opened the existing file with O_TRUNC and then narrowed its mode
    through the descriptor. Anyone who had opened the 0644 file beforehand kept
    a readable descriptor on the same inode and read the token as it landed.
    A new file renamed over the target leaves that descriptor on the old inode.
    """
    out = tmp_path / "kc.yaml"
    out.write_text("old\n", encoding="utf-8")
    os.chmod(out, 0o644)
    with open(out, encoding="utf-8") as stale:
        kc._write_kubeconfig_file(out, KUBECONFIG)
        stale.seek(0)
        assert stale.read() == "old\n"
    assert out.read_text(encoding="utf-8") == KUBECONFIG
    assert_owner_only(out)
    assert [p.name for p in tmp_path.iterdir()] == ["kc.yaml"], "temp file left behind"


def _disk_full(*args, **kwargs):
    raise OSError(28, "No space left on device")


@pytest.mark.unit
@pytest.mark.parametrize(
    "step", [s for s in ("fchmod", "fsync", "replace") if hasattr(os, s)]
)
def test_a_failed_export_leaves_the_existing_kubeconfig_untouched(tmp_path, monkeypatch, step):
    """The old writer truncated first: a later failure destroyed the user's file."""
    out = tmp_path / "kc.yaml"
    out.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(kc.os, step, _disk_full)
    with pytest.raises(ValueError):
        kc._write_kubeconfig_file(out, KUBECONFIG)
    assert out.read_text(encoding="utf-8") == "old\n"
    assert [p.name for p in tmp_path.iterdir()] == ["kc.yaml"], "temp file left behind"
