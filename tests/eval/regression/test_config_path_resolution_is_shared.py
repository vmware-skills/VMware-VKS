"""The CLI, the doctor and the MCP server must open the same config file.

Swept for after the same defect was found on real hardware in the sibling Aria
skill, 2026-08-30. This skill had the most copies of the rule of any in the
family — five, and they did not agree:

* ``config.load_config`` resolved ``config_path or CONFIG_FILE`` and never
  looked at ``VMWARE_VKS_CONFIG`` at all;
* ``cli._get_si`` read the variable itself and passed the result down;
* ``cli.cmd_preflight_auth`` read it again to enumerate targets;
* ``preflight_auth._connect_step`` read it a third time;
* ``mcp_server.server._get_conn_mgr`` read it a fourth;
* and ``doctor.run_doctor`` resolved ``config_path or CONFIG_FILE``, skipping
  the variable, then passed that path *explicitly* to ``load_config`` — which
  suppressed the variable there too. So the doctor did not merely inspect the
  wrong file, it made itself internally consistent with the wrong one and
  produced a green report for a configuration nothing else would read.

Reproduced before the fix, against a real config at the default path and the
variable pointing elsewhere::

    load_config()  -> targets: ['home-vcenter']   # doctor, and some CLI paths
    mtime_cached() -> targets: ['from-env']       # MCP server

Two different vCenters in one installation, selected by which surface you came
in through — and inside the CLI itself, ``vmware-vks check`` and
``vmware-vks preflight-auth`` disagreed with each other about which Supervisor
they were validating.

``VMWARE_VKS_CONFIG`` is this skill's advertised ``primaryEnv`` in its OpenClaw
metadata, so the surfaces that honoured it were right and ``load_config`` was
the one that was wrong.

The precedence now lives in exactly one function, ``resolve_config_path``, that
every reader goes through — copies of a rule do not disagree loudly, they
disagree slowly, which is how these five drifted (CLAUDE.md 形态 #6).
"""

from __future__ import annotations

import inspect

import pytest

from vmware_vks import config as cfg
from vmware_vks import doctor as doc

# Deliberately different target counts *and* target names. The count says
# which file was parsed; the names say which file the per-target checks below
# it were driven from.
#
# The sibling AIops and Monitor doctors are distinguished by hostname, because
# their connectivity check prints the host it dialled. This one is not: when a
# password is missing it records that and never reaches a network step, so no
# host ever reaches the report. The per-target rows ("Password (a)") are the
# signal that does — they come from the parsed config, which is the thing being
# resolved.
_DEFAULT_TARGET = "only-in-the-default"

_ONE_TARGET = f"""
targets:
  - name: {_DEFAULT_TARGET}
    host: default.invalid
    port: 443
    username: admin
"""

_THREE_TARGETS = """
targets:
  - name: from-the-env-var-a
    host: a.invalid
    port: 443
    username: admin
  - name: from-the-env-var-b
    host: b.invalid
    port: 443
    username: admin
  - name: from-the-env-var-c
    host: c.invalid
    port: 443
    username: admin
"""


def _flat(text: str) -> str:
    """The report with whitespace and table drawing removed.

    Rich wraps a long path across cells, so flattening keeps the assertions
    about *which file* independent of the table layout.
    """
    return "".join(ch for ch in text if not ch.isspace() and ch not in "│┃")


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """A default config and .env that are both entirely valid.

    The point of making the default healthy is that the only way the doctor can
    end up reporting on the variable's file is by resolving it — a red report
    for an unrelated reason would prove nothing.
    """
    default = tmp_path / "default.yaml"
    default.write_text(_ONE_TARGET)
    env_file = tmp_path / "dot.env"
    env_file.write_text("")
    env_file.chmod(0o600)

    monkeypatch.setattr(cfg, "CONFIG_FILE", default)
    monkeypatch.setattr(cfg, "ENV_FILE", env_file)
    monkeypatch.delenv("VMWARE_VKS_CONFIG", raising=False)
    # Rich elides long details at 80 columns, so an assertion about a tmp_path
    # would be measuring the terminal rather than the doctor.
    monkeypatch.setenv("COLUMNS", "300")
    return default


def test_the_env_var_decides_which_file_is_resolved(sandbox, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere.yaml"
    elsewhere.write_text(_THREE_TARGETS)
    monkeypatch.setenv("VMWARE_VKS_CONFIG", str(elsewhere))

    assert cfg.resolve_config_path() == elsewhere
    assert len(cfg.load_config().targets) == 3, (
        "load_config ignored $VMWARE_VKS_CONFIG, so the doctor reads one file "
        "and the MCP server another"
    )


def test_an_explicit_path_still_beats_the_env_var(sandbox, tmp_path, monkeypatch):
    """The control on precedence: `--config` is the operator saying which file
    they mean, and it has to keep winning.

    A "fix" that let the variable overtake the flag would pass the test above
    and break `vmware-vks check --config`.
    """
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(_ONE_TARGET)
    monkeypatch.setenv("VMWARE_VKS_CONFIG", str(tmp_path / "ignored.yaml"))

    assert cfg.resolve_config_path(explicit) == explicit
    assert len(cfg.load_config(explicit).targets) == 1


def test_with_neither_it_is_the_default(sandbox):
    assert cfg.resolve_config_path() == cfg.CONFIG_FILE
    assert len(cfg.load_config().targets) == 1


def test_the_cli_and_the_mcp_server_open_the_same_file(sandbox, tmp_path, monkeypatch):
    """The defect itself, end to end, against the server's real loader.

    A structural test alone would not have caught this: each surface was
    internally tidy, they simply disagreed. So this asserts on the thing that
    was wrong — the two paths returning different vCenters.
    """
    from vmware_vks.mcp_server import server

    elsewhere = tmp_path / "elsewhere.yaml"
    elsewhere.write_text(_THREE_TARGETS)
    monkeypatch.setenv("VMWARE_VKS_CONFIG", str(elsewhere))

    cli_targets = [t.name for t in cfg.load_config().targets]
    server_targets = [t.name for t in server._cached_config().targets]

    assert cli_targets == server_targets, (
        f"the CLI loaded {cli_targets} and the MCP server loaded "
        f"{server_targets}: one installation, two vCenters, chosen by which "
        f"surface you came in through"
    )


def test_doctor_does_not_pass_while_the_tools_cannot_load_the_config(
    sandbox, tmp_path, monkeypatch, capsys
):
    """The reported failure: the doctor clears a configuration while every tool
    call raises FileNotFoundError.

    The default config here exists and parses. It is simply not the file the
    tools will open.
    """
    missing = tmp_path / "not-there.yaml"
    monkeypatch.setenv("VMWARE_VKS_CONFIG", str(missing))

    with pytest.raises(FileNotFoundError):
        cfg.load_config()

    ok = doc.run_doctor()
    out = _flat(capsys.readouterr().out)

    assert ok is False, (
        "doctor passed against a config file that does not exist; this is the "
        "report that tells an operator their broken setup is fine"
    )
    assert str(missing) in out, (
        "the report must name the file it looked at — a verdict about an "
        "unnamed file is what made this take real hardware to find"
    )
    assert "1target(s)" not in out, (
        "doctor parsed the default config and reported on it while every tool "
        "call raises FileNotFoundError on the path in $VMWARE_VKS_CONFIG"
    )
    assert _DEFAULT_TARGET not in out, (
        "a per-target row was driven from the default config; with the "
        "variable set, nothing should be looking at that file at all"
    )


def test_doctor_reads_the_env_vars_file_not_the_default(
    sandbox, tmp_path, monkeypatch, capsys
):
    """The positive half: pointed at a real file elsewhere, the doctor reports
    on that one — three targets, not the default's one."""
    elsewhere = tmp_path / "elsewhere.yaml"
    elsewhere.write_text(_THREE_TARGETS)
    monkeypatch.setenv("VMWARE_VKS_CONFIG", str(elsewhere))

    doc.run_doctor()
    out = _flat(capsys.readouterr().out)

    assert str(elsewhere) in out, "the report must name the file it looked at"
    assert "3target(s)" in out, (
        "the doctor counted the default file's targets, so it parsed the file "
        "the tools will never open"
    )
    assert "Password(from-the-env-var-a)" in out, (
        "the per-target checks were not driven from the variable's file"
    )
    assert _DEFAULT_TARGET not in out, (
        "a per-target row was driven from the default config — the target "
        "count above cannot see that, which is why this is here"
    )


def test_an_explicit_config_path_still_reaches_the_doctor(
    sandbox, tmp_path, monkeypatch, capsys
):
    """`vmware-vks check --config <path>` keeps winning over the variable.

    The doctor is the one surface that takes an explicit path, so the
    precedence control has to be exercised here too and not only on the
    resolver.
    """
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(_THREE_TARGETS)
    monkeypatch.setenv("VMWARE_VKS_CONFIG", str(tmp_path / "ignored.yaml"))

    doc.run_doctor(explicit)
    out = _flat(capsys.readouterr().out)

    assert str(explicit) in out
    assert "3target(s)" in out, "the flag did not decide which file was parsed"


def test_load_config_and_the_doctor_cannot_disagree():
    """Structural, not behavioural: every reader goes through the one resolver,
    so a future edit cannot silently desynchronise them again."""
    assert "resolve_config_path" in inspect.getsource(cfg.load_config), (
        "load_config resolves the config path by itself again; that is the "
        "duplication this test exists to prevent"
    )
    assert "CONFIG_FILE" not in inspect.getsource(doc), (
        "the doctor names the default config path directly, so it can diagnose "
        "a file the tools will not open"
    )


def test_no_other_surface_keeps_its_own_copy_of_the_precedence():
    """The other four copies.

    Each of these read $VMWARE_VKS_CONFIG itself and passed the result down
    explicitly, which is why the surfaces disagreed — including two inside the
    CLI, so `vmware-vks check` and `vmware-vks preflight-auth` could validate
    different Supervisors in the same shell.

    Asserted on ``os.environ`` rather than on the variable's name: a grep for
    the name cannot tell a read from a docstring that mentions it.
    """
    from vmware_vks import cli, preflight_auth
    from vmware_vks.mcp_server import server

    for fn in (
        cli._get_si,
        cli.cmd_preflight_auth,
        preflight_auth._connect_step,
        server._get_conn_mgr,
    ):
        assert "os.environ" not in inspect.getsource(fn), (
            f"{fn.__qualname__} resolves the config path itself; let "
            f"load_config do it"
        )
