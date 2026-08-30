"""A failed check must be named for the stage that failed.

Found on a real vCenter, 2026-08-30. `vmware-vks check` printed both:

    vCenter reachable (home-vcenter)   ✓ PASS   v8.0.3
    vCenter reachable (home-vcenter)   ✗ FAIL   REST GET /vcenter/namespace-
                                                management/clusters failed (401)

One `try` wrapped four distinct checks — connect, version, and the Workload
Management query — and the single `except` labelled every failure "vCenter
reachable". vCenter was reachable; the row above says so. What failed was WCP.

A diagnostic that names the wrong layer sends the operator to check networking
and credentials that are already fine, which is the same defect shape as a
remedy pointing at a check that passes.
"""

from __future__ import annotations

import pathlib

from vmware_vks import doctor


class _T:
    name = "home-vcenter"
    host = "192.0.2.1"
    port = 443
    username = "administrator@vsphere.local"
    password = "x"
    verify_ssl = False


class _Cfg:
    def __init__(self):
        self.targets = [_T()]


def _run(monkeypatch, capsys, fail_at) -> str:
    """Run the doctor with a connection that fails at a chosen stage, and
    return its rendered table as one line (Rich wraps to terminal width)."""
    import vmware_vks.config as cfgmod
    import vmware_vks.connection as conn
    import vmware_vks.ops.supervisor as sup

    monkeypatch.setattr(cfgmod, "load_config", lambda *a, **k: _Cfg())
    # The doctor asks config.resolve_config_path() since 2026-08-30, so it no
    # longer reads a CONFIG_FILE attribute off this module — the patch that
    # used to sit here had `raising=False` and was already setting an attribute
    # nothing read. Point the default at a missing path where the resolver
    # actually looks, and clear the override so the ambient environment cannot
    # decide which file this test is about.
    monkeypatch.delenv("VMWARE_VKS_CONFIG", raising=False)
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", pathlib.Path("/nonexistent"))

    class Mgr:
        def __init__(self, cfg):
            pass

        def connect(self, name):
            if fail_at == "connect":
                raise RuntimeError("could not open a session")
            about = type("A", (), {"version": "8.0.3"})()
            return type("SI", (), {"content": type("C", (), {"about": about})()})()

    monkeypatch.setattr(conn, "ConnectionManager", Mgr)

    def _get(si, path):
        if fail_at == "wcp":
            raise RuntimeError("REST GET /vcenter/namespace-management/clusters failed (401)")
        return []

    monkeypatch.setattr(sup, "_rest_get", _get)
    doctor.run_doctor()
    return " ".join(capsys.readouterr().out.split())


def _failed_labels(rendered: str) -> str:
    """The labels on rows that failed. Rich renders one row per line group; the
    marker travels with its label, so proximity is enough for this assertion."""
    return rendered


def test_a_wcp_failure_is_not_labelled_a_connectivity_failure(monkeypatch, capsys):
    out = _run(monkeypatch, capsys, "wcp")
    # vCenter answered with its version, so the reachability row passed.
    assert "8.0.3" in out
    # The failing row must name Workload Management, not reachability.
    assert "WCP" in out or "Workload" in out, out[:400]
    assert out.count("vCenter reachable") == 1, (
        f"'vCenter reachable' appears {out.count('vCenter reachable')} times — "
        f"once passing and once failing for a WCP error: {out[:400]}"
    )


def test_a_real_connect_failure_is_still_labelled_connectivity(monkeypatch, capsys):
    out = _run(monkeypatch, capsys, "connect")
    assert "vCenter reachable" in out
    assert "could not open a session" in out
