"""Every CLI command that performs a write is wrapped by @guarded (HLD I-1, I-8).

A write CLI command must route through vmware_policy's guard() + audit_call() —
the same enforcement @vmware_tool gives the MCP surface — so ``vmware-vks tkc
delete`` run through Bash is authorized and audited to ~/.vmware/audit.db exactly
like the ``delete_tkc_cluster`` MCP tool. Without @guarded a CLI write bypassed
policy and landed only in the legacy per-skill log (the gap HLD §2.1 documents).

The write set is DERIVED, never hand-listed (踩坑 #43): a tool annotated
``readOnlyHint=False`` is a write; the ops functions its body calls — reached by
a bare name OR ``module.func`` on an ops-module import — are the state-changing
ops; a CLI ``@command`` calling one is a write command and must carry @guarded.
The attribute-call case is not optional: the server tools reach ops as
``_ns.delete_namespace`` / ``_tkc.delete_tkc_cluster`` (module-alias attribute
calls) while cli.py imports the real names and calls them bare — a derivation
blind to either style would silently miss the whole write surface, the "label
promises more than content" shape.

VKS ships a single ``cli.py`` and a single ``mcp_server/server.py`` rather than
AIops's ``cli/`` and ``mcp_server/tools/`` packages; path-discovery below detects
either layout. Pointing rglob at a directory that does not exist would return
zero files and pass vacuously — the "empty results read as no problem" shape —
so the anti-vacuous asserts stay.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib

_REPO = pathlib.Path(__file__).resolve().parents[3]
_PKG = _REPO / "vmware_vks"


def _cli_files() -> list[pathlib.Path]:
    """The CLI source file(s): a ``cli/`` package if present, else ``cli.py``."""
    cli_dir = _PKG / "cli"
    if cli_dir.is_dir():
        files = sorted(cli_dir.rglob("*.py"))
        assert files, f"CLI package {cli_dir} has no .py files — scan would be vacuous"
        return files
    cli_file = _PKG / "cli.py"
    assert cli_file.is_file(), f"no CLI at {cli_dir}/ or {cli_file} — scan would be vacuous"
    return [cli_file]


def _tools_dir() -> pathlib.Path:
    """Where the @mcp.tool definitions live: ``mcp_server/tools/`` or ``mcp_server/``."""
    tools = _PKG / "mcp_server" / "tools"
    d = tools if tools.is_dir() else _PKG / "mcp_server"
    assert d.is_dir(), f"MCP tools not found at {d} — the derivation would be empty"
    return d


def _write_tool_names() -> frozenset[str]:
    from vmware_vks.mcp_server.server import mcp

    return frozenset(
        t.name
        for t in asyncio.run(mcp.list_tools())
        if getattr(getattr(t, "annotations", None), "readOnlyHint", None) is False
    )


def _ops_refs(tree: ast.AST) -> tuple[dict[str, str], set[str]]:
    """(local name -> REAL ops function name, ops-module aliases).

    An aliased import (``from ops.mod import realname as _alias``) maps
    ``_alias -> realname`` so an aliased call resolves to the same op an
    un-aliased import names. The server aliases ops modules
    (``from vmware_vks.ops import namespace as _ns``) and calls ``_ns.func``
    while cli.py imports the real names and calls them bare; both must derive to
    the real name or the MCP→ops→CLI intersection is empty (the shape that hides
    a whole write surface).
    """
    func_map: dict[str, str] = {}
    mods: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            parts = n.module.split(".")
            if "ops" in parts:
                if parts[-1] == "ops":
                    mods.update(a.asname or a.name for a in n.names)
                else:
                    for a in n.names:
                        func_map[a.asname or a.name] = a.name
    return func_map, mods


def _ops_calls(node: ast.AST, func_map: dict[str, str], mods: set[str]) -> set[str]:
    """Real ops function names called in ``node`` — via ``f()`` or ``mod.f()``."""
    out: set[str] = set()
    for c in ast.walk(node):
        if not isinstance(c, ast.Call):
            continue
        f = c.func
        if isinstance(f, ast.Name) and f.id in func_map:
            out.add(func_map[f.id])
        elif (
            isinstance(f, ast.Attribute)
            and isinstance(f.value, ast.Name)
            and f.value.id in mods
        ):
            out.add(f.attr)
    return out


def _write_ops() -> frozenset[str]:
    targets = _write_tool_names()
    assert targets, "no write tools (readOnlyHint=False) — derivation vacuous"
    ops: set[str] = set()
    for path in sorted(_tools_dir().rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        func_map, mods = _ops_refs(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in targets:
                ops |= _ops_calls(node, func_map, mods)
    return frozenset(ops)


def _decorator_names(node: ast.FunctionDef) -> set[str]:
    names: set[str] = set()
    for d in node.decorator_list:
        t = d.func if isinstance(d, ast.Call) else d
        if isinstance(t, ast.Name):
            names.add(t.id)
        elif isinstance(t, ast.Attribute):
            names.add(t.attr)
    return names


def _cli_write_commands() -> tuple[list[str], list[str]]:
    """(write commands, of those the ones missing @guarded)."""
    write_ops = _write_ops()
    assert write_ops, "no write ops derived — vacuous"
    writing: list[str] = []
    unguarded: list[str] = []
    for path in _cli_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        func_map, mods = _ops_refs(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if not any(
                isinstance(d, ast.Call)
                and isinstance(getattr(d, "func", None), ast.Attribute)
                and d.func.attr == "command"
                for d in node.decorator_list
            ):
                continue
            if _ops_calls(node, func_map, mods) & write_ops:
                label = f"{path.name}:{node.name}"
                writing.append(label)
                if "guarded" not in _decorator_names(node):
                    unguarded.append(label)
    return writing, unguarded


def test_every_write_cli_command_is_guarded():
    writing, unguarded = _cli_write_commands()
    # namespace create/update/delete, tkc create/scale/upgrade/delete, and the two
    # kubeconfig exports (credential access, guarded like writes).
    assert len(writing) >= 9, (
        f"only {len(writing)} write CLI commands derived ({writing}) — the "
        f"MCP→ops→CLI derivation is likely stale; a check matching almost nothing "
        f"is worse than none."
    )
    assert not unguarded, (
        f"these CLI commands call a [WRITE] ops function but are not @guarded, so "
        f"they bypass policy + audit (HLD I-1): {unguarded}"
    )


def _op_to_mcp_tools() -> dict[str, set[str]]:
    """Write ops function -> the MCP write tools whose body calls it."""
    targets = _write_tool_names()
    out: dict[str, set[str]] = {}
    for path in sorted(_tools_dir().rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        func_map, mods = _ops_refs(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in targets:
                for op in _ops_calls(node, func_map, mods):
                    out.setdefault(op, set()).add(node.name)
    return out


# Guarded CLI commands with no MCP write twin derivable from the ops function they
# call, each with the reason. Empty today: every guarded command maps to exactly
# one MCP write tool. A guarded command missing from the derivation used to be
# skipped silently — it was neither checked nor reported — so a rename or a
# derivation that stopped resolving it passed. Now the skipped set must equal
# this list exactly, and adding to it needs a stated reason.
_NO_DERIVED_TWIN: dict[str, str] = {}


def test_guarded_cli_writes_carry_their_mcp_tool_name():
    """A deny rule names a tool; it must stop the CLI twin of that tool too (HLD I-3).

    ``@guarded`` defaults the tool name to the function's ``__name__``, so
    ``tkc delete`` was guarded as ``tkc_delete`` while its MCP twin is
    ``delete_tkc_cluster`` — a rule denying ``delete_tkc_cluster`` refused the
    agent and let the same delete through the CLI, and the two surfaces wrote
    the one audit sink under two names. The twin is DERIVED: the MCP write tool
    that calls the same ops function the command calls.
    """
    from vmware_vks import cli
    from vmware_vks.mcp_server import server as srv

    op_tools = _op_to_mcp_tools()
    write_ops = frozenset(op_tools)
    checked: list[str] = []
    skipped: list[str] = []
    mismatched: list[str] = []
    for path in _cli_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        func_map, mods = _ops_refs(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            ops = _ops_calls(node, func_map, mods) & write_ops
            fn = getattr(cli, node.name, None)
            if not getattr(fn, "_is_guarded", False):
                continue  # unguarded writes are the test above's finding
            if not ops:
                skipped.append(node.name)
                continue
            twins = set().union(*(op_tools[o] for o in ops))
            assert len(twins) == 1, (
                f"{node.name} maps to several MCP tools {sorted(twins)} — pick "
                f"the one it mirrors explicitly"
            )
            (twin,) = twins
            checked.append(node.name)
            if fn._guarded_tool != twin:
                mismatched.append(f"{node.name}: guarded as {fn._guarded_tool!r}, MCP tool {twin!r}")
            elif fn._risk_level != getattr(srv, twin)._risk_level:
                mismatched.append(
                    f"{node.name}: risk {fn._risk_level!r}, MCP tool {twin!r} "
                    f"risk {getattr(srv, twin)._risk_level!r}"
                )
    assert sorted(skipped) == sorted(_NO_DERIVED_TWIN), (
        f"guarded CLI commands with no derived MCP twin: {sorted(skipped)}, expected "
        f"{sorted(_NO_DERIVED_TWIN)} — their name and risk went unchecked. Fix the "
        f"derivation, or list the command in _NO_DERIVED_TWIN with a reason"
    )
    assert len(checked) >= 9, f"only {checked} checked — derivation likely stale"
    assert not mismatched, (
        "these CLI writes are guarded under a different name or risk than their "
        "MCP tool, so one deny rule does not scope both surfaces — pass the MCP "
        "tool name to @guarded(...): " + "; ".join(mismatched)
    )


def test_high_blast_radius_commands_are_derived_and_guarded():
    """Pin named commands so a broad-but-wrong derivation cannot pass the floor.

    ``namespace_delete`` and ``tkc_delete`` are the two destructiveHint=True
    writes; their presence proves the readOnlyHint→ops→command chain resolves
    the server's ``_ns.delete_namespace`` / ``_tkc.delete_tkc_cluster`` attribute
    calls all the way to the bare-name calls in cli.py.
    """
    writing, _ = _cli_write_commands()
    names = {w.split(":", 1)[1] for w in writing}
    for must in ("namespace_delete", "tkc_delete"):
        assert must in names, (
            f"{must} is no longer derived as a write command — the readOnlyHint→"
            f"ops→command derivation stopped resolving it"
        )
