"""The MCP server's instructions must name the configured targets.

``initialize`` hands the client the server's ``instructions``, and for a skill
whose every tool takes ``target`` that text is the only place the client learns
which targets exist. Shipped empty, the model calls tools with no ``target``,
gets whatever the connection layer defaults to, and answers confidently about
the wrong system. Monitor shipped that way until 2026-09-15: its first
configured target was a standalone ESXi host, so "how many VMs does the vCenter
have" was answered from that host, and an estate that had a vCenter was told it
had none.

Here the same silence has a sharper edge. A vSphere Namespace or TKC lives in
exactly one Supervisor, so a name that resolves under one target need not exist
under another — and this skill's own not-found errors are written to be
believed. Asked about a cluster in the second vCenter with no target given, the
model queries the first and reports a cluster that exists as missing.

The listing has to be computed from the loaded config: a hardcoded sentence
drifts the day the operator edits their file. So these tests drive the builder
with a monkeypatched loader and never require a config on disk — the point is
what the builder does with a config, not that this machine has one.
"""

from __future__ import annotations

import pytest

from vmware_vks.config import AppConfig, TargetConfig
from vmware_vks.mcp_server import server

# Names no config on any developer's machine would contain, so a name appearing
# in the built text can only have come from the config handed to the builder.
_FIRST = TargetConfig(
    name="zulu-supervisor-7421",
    host="10.77.4.21",
    config_username="svc-vks@vsphere.local",
)
_SECOND = TargetConfig(
    name="yankee-supervisor-7422",
    host="10.77.4.22",
    config_username="svc-vks@vsphere.local",
)


def _loaded_config(*_args: object, **_kwargs: object) -> AppConfig:
    return AppConfig(targets=(_FIRST, _SECOND))


@pytest.mark.unit
def test_instructions_name_every_configured_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "load_config", _loaded_config)

    text = server._target_instructions()

    assert "Configured targets:" in text
    # This skill's TargetConfig carries no `type` field — every target is a
    # vCenter running a Supervisor — so the kind word is a constant and the
    # per-target facts that vary are the name and the host.
    assert "zulu-supervisor-7421 (vcenter, 10.77.4.21, default)" in text
    assert "yankee-supervisor-7422 (vcenter, 10.77.4.22)" in text


@pytest.mark.unit
def test_the_default_marked_is_the_one_connect_would_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the target ``ConnectionManager.connect`` falls back to is marked default.

    ``connect(None)`` uses ``AppConfig.default_target``, so the listing asks that
    property rather than assuming a position. Marking the wrong entry would be
    worse than marking none: it would send the model to the target it was trying
    to warn it about.
    """
    monkeypatch.setattr(server, "load_config", _loaded_config)

    text = server._target_instructions()

    assert _loaded_config().default_target.name == "zulu-supervisor-7421"
    assert text.count(", default)") == 1
    assert "yankee-supervisor-7422 (vcenter, 10.77.4.22, default)" not in text


@pytest.mark.unit
def test_instructions_carry_both_marker_phrases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "load_config", _loaded_config)

    text = server._target_instructions()

    assert "Configured targets:" in text
    assert "Choosing a target:" in text


@pytest.mark.unit
def test_an_unreadable_config_still_names_the_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A config that cannot be read must be said so under the marker, not omitted.

    Dropping the listing was the first fix's blind spot, and it was blind in the
    only state where the defect can appear: a machine that has installed the skill
    and not yet written a config. Shown no listing at all, a client cannot tell
    "this skill has no targets" from "this skill could not read them" — and the
    first reading is the one that produces a confident answer about a system
    nobody chose, which is the whole defect this text exists to prevent.
    """

    def _explode(*_args: object, **_kwargs: object) -> AppConfig:
        raise FileNotFoundError("/private/nowhere-secret/config.yaml not found")

    monkeypatch.setattr(server, "load_config", _explode)

    text = server._target_instructions()  # must not raise

    assert "Configured targets: could not be read (FileNotFoundError)" in text
    assert "run `vmware-vks doctor`" in text
    assert "Choosing a target:" in text
    # Only the exception's TYPE. Its message quotes the resolved config path, and
    # these instructions go to the client verbatim on every `initialize`.
    assert "/private/nowhere-secret" not in text
    assert "nowhere-secret" not in text


@pytest.mark.unit
def test_a_config_with_no_targets_says_none_yet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero targets is readable-but-empty, and needs its own remedy.

    ``AppConfig.default_target`` raises on an empty target list, so this is also
    the case that would crash the builder if the listing asked for a default
    before checking. The remedy is to add a target, not to run the doctor.
    """
    monkeypatch.setattr(server, "load_config", lambda *_a, **_k: AppConfig(targets=()))

    text = server._target_instructions()  # must not raise

    assert "Configured targets: none yet" in text
    assert "~/.vmware-vks/config.yaml" in text
    assert "Choosing a target:" in text
    # Not the unreadable-config wording: the two states have different remedies.
    assert "could not be read" not in text
    assert ", default)" not in text


@pytest.mark.unit
def test_the_running_server_was_given_instructions() -> None:
    """The instructions reach FastMCP, not just the builder.

    Independent of whether this machine has a config: both branches of
    ``_target_instructions`` carry the rule.
    """
    assert server.mcp.instructions
    assert "Choosing a target:" in server.mcp.instructions
