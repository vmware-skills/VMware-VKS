"""Session-wide sandbox: the suite must not touch the operator's real files.

Installed at *import* time, not in a fixture. ``vmware_vks.notify.audit`` binds
``Path.home() / ".vmware-vks"`` when the module is imported, and a fixture — even
a session-scoped autouse one — runs after collection has already imported every
test module and, with them, the package. By the time a fixture could redirect
``HOME`` the path is a constant.

Two variables, because the skill writes two audit trails:

* ``OPS_HOME`` moves ``vmware_policy``'s shared ``audit.db`` (and the policy,
  budget and undo state beside it). ``vmware_policy.paths.ops_home()`` reads it
  on every call and defaults to ``~/.vmware``.
* ``HOME`` moves ``~/.vmware-vks/audit.log``, the per-skill JSON Lines log,
  which resolves through ``Path.home()`` and so ignores ``OPS_HOME`` entirely.

Before this existed, ``pytest`` appended to both — including a
``delete_tkc_cluster {"confirmed": true}`` row for a cluster that never existed.
An audit trail that contains test fiction cannot answer the question it is kept
to answer. See ``tests/eval/regression/test_audit_isolation.py``, which asserts
this sandbox is in place so a future test cannot quietly do without it.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

from vmware_policy.audit import reset_engine

# The operator's real home, captured before the redirect. The regression test
# expresses "not the real audit database" against this.
REAL_HOME = Path(os.path.expanduser("~"))

SANDBOX_HOME = Path(tempfile.mkdtemp(prefix="vmware-vks-tests-"))

os.environ["HOME"] = str(SANDBOX_HOME)
os.environ["OPS_HOME"] = str(SANDBOX_HOME / ".vmware")
# expanduser() consults USERPROFILE on Windows and, on POSIX, falls back to the
# password database when HOME is unset; keep every spelling pointing here so the
# sandbox holds on the family's Windows test host too.
os.environ["USERPROFILE"] = str(SANDBOX_HOME)

# vmware_policy's audit engine is a lazily built singleton keyed to the path it
# first resolved. Nothing should have built one this early, but a stale binding
# would silently send every write back to the real file, so clear it. Imported
# unguarded on purpose: vmware_policy is a hard dependency of this skill, and a
# swallowed ImportError here would leave the sandbox half-installed and quiet.
reset_engine()

atexit.register(shutil.rmtree, SANDBOX_HOME, True)
