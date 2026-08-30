"""The test suite must never write to the operator's real audit trail.

Real-hardware finding, 2026-08-30 (VCF 9.1): running ``pytest`` in this repo
appended rows to the operator's live ``~/.vmware/audit.db`` and to
``~/.vmware-vks/audit.log`` — among them
``delete_tkc_cluster {"confirmed": true}``, an event that never happened on any
vCenter. 306 vks rows had accumulated in the shared database and 130 KB in the
JSON Lines log.

That is wrong twice. It corrupts the operator's own record of what was done to
their estate, and it means a compliance artefact — the file that answers "who
deleted this cluster" — contains fiction that a reader cannot tell from fact.

The fix is a sandbox installed in ``tests/conftest.py`` *at import time*, before
``vmware_vks`` is imported: ``HOME`` and ``OPS_HOME`` both point into a
temporary directory. The tests below are the assertion half — a future test that
forgets to isolate itself cannot reach the real file, because there is no code
path from the sandboxed environment to it.

Both halves are needed. ``OPS_HOME`` covers the shared ``vmware_policy``
database; it does *not* cover ``vmware_vks.notify.audit``, which binds
``Path.home() / ".vmware-vks"`` at module import and was the source of the
JSON Lines pollution.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

# The real home, captured by conftest before it redirected HOME. Everything
# here is expressed relative to it, so the assertions keep meaning on any
# machine and in CI.
from tests.conftest import REAL_HOME, SANDBOX_HOME


def _under(path: Path, root: Path) -> bool:
    return root.resolve() in path.resolve().parents or path.resolve() == root.resolve()


def test_sandbox_is_not_the_real_home() -> None:
    """Positive control: the sandbox must actually be somewhere else."""
    assert SANDBOX_HOME.resolve() != REAL_HOME.resolve()
    assert not _under(SANDBOX_HOME, REAL_HOME), (
        "the sandbox lives inside the real home — a stray absolute path would "
        "still land on the operator's files"
    )


def test_home_and_ops_home_point_into_the_sandbox() -> None:
    assert Path(os.environ["HOME"]).resolve() == SANDBOX_HOME.resolve()
    assert _under(Path(os.environ["OPS_HOME"]), SANDBOX_HOME)
    assert Path.home().resolve() == SANDBOX_HOME.resolve()


def test_policy_resolves_the_shared_audit_db_inside_the_sandbox() -> None:
    from vmware_policy.paths import ops_path

    db = ops_path("audit.db")
    assert _under(db, SANDBOX_HOME)
    assert db.resolve() != (REAL_HOME / ".vmware" / "audit.db").resolve()


def test_the_live_audit_engine_writes_inside_the_sandbox() -> None:
    """The singleton, not just the path helper.

    ``get_engine()`` caches its database path on first use. If anything
    constructed it before the sandbox was installed, the helper above would
    still read clean while every real write went to the operator's file.
    """
    from vmware_policy.audit import get_engine

    engine_path = Path(get_engine()._path)
    assert _under(engine_path, SANDBOX_HOME), (
        f"the audit singleton is bound to {engine_path}, outside the sandbox — "
        "something initialised it before conftest set OPS_HOME"
    )


def test_the_skill_json_lines_log_is_inside_the_sandbox() -> None:
    """The half OPS_HOME does not cover.

    ``notify.audit`` binds its directory from ``Path.home()`` at import, so
    only a redirected ``HOME`` moves it.
    """
    from vmware_vks.notify.audit import _AUDIT_FILE

    assert _under(_AUDIT_FILE, SANDBOX_HOME)


def test_an_audited_write_lands_in_the_sandbox_and_not_in_the_real_db() -> None:
    """End to end, through the decorator the MCP tools actually use.

    A path assertion alone would pass against a database nothing writes to.
    This emits a row that is recognisably from this test, then proves it is in
    the sandbox database and absent from the operator's.
    """
    from vmware_policy import get_engine

    marker = "test_audit_isolation_probe"
    get_engine().log(skill="vks", tool=marker, params={}, result={}, status="ok")

    sandbox_db = Path(get_engine()._path)
    assert sandbox_db.exists(), "the sandbox database was never created"
    with sqlite3.connect(f"file:{sandbox_db}?mode=ro", uri=True) as con:
        found = con.execute(
            "SELECT count(*) FROM audit_log WHERE tool = ?", (marker,)
        ).fetchone()[0]
    assert found >= 1, "the row did not reach the sandbox database"

    real_db = REAL_HOME / ".vmware" / "audit.db"
    if not real_db.exists():
        pytest.skip("no production audit database on this machine to check against")
    with sqlite3.connect(f"file:{real_db}?mode=ro", uri=True) as con:
        leaked = con.execute(
            "SELECT count(*) FROM audit_log WHERE tool = ?", (marker,)
        ).fetchone()[0]
    assert leaked == 0, (
        f"{leaked} row(s) from this test reached the operator's audit database "
        f"at {real_db}"
    )
