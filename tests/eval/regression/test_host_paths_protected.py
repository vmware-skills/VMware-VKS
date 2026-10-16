"""Regression: a kubeconfig could be written over this server's own files (HLD §18 step 0).

``issue_*_kubeconfig(output_path=…)`` writes a host file; over HTTPS any token
holder could have it overwrite ``$OPS_HOME/mcp-tokens`` or ``rules.yaml``. The
writer now refuses a path that resolves into ``$OPS_HOME`` or a ``~/.vmware-*``
directory, for ``str`` and ``Path`` alike.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from vmware_policy.hostpaths import HostPathRefusedError

from vmware_vks.ops.kubeconfig import _write_kubeconfig_file


@pytest.mark.parametrize("as_path", [True, False])
def test_a_kubeconfig_is_not_written_into_ops_home(as_path):
    home = Path(os.environ["OPS_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    tokens = home / "mcp-tokens"
    tokens.write_text("alice:abc\n", encoding="utf-8")
    with pytest.raises(HostPathRefusedError):
        _write_kubeconfig_file(tokens if as_path else str(tokens), "apiVersion: v1\n")
    assert tokens.read_text(encoding="utf-8") == "alice:abc\n"


def test_a_kubeconfig_elsewhere_is_still_written(tmp_path):
    out = _write_kubeconfig_file(tmp_path / "kc.yaml", "apiVersion: v1\n")
    assert Path(out).read_text(encoding="utf-8") == "apiVersion: v1\n"
