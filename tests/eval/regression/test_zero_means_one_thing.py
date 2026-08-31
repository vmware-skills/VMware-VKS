"""A zero this skill returns must say which kind of zero it is.

Round 3 of the VCF 9 field testing, VKS technical debt 1 and 2. Two findings,
one shape:

* ``check_vks_compatibility`` wrapped its REST call in ``except Exception:
  clusters = []``. Against a healthy vCenter with no Supervisor it returned
  ``{"compatible": true, "wcp_enabled_clusters": 0, "wcp_clusters": [],
  "hint": null}``. Against the same vCenter with its REST session poisoned it
  returned the byte-for-byte identical payload. This is the tool the rest of
  the skill points at -- ``k8s_connection`` says "Run check_vks_compatibility
  to confirm this vCenter supports VKS" -- so for the whole of the round-2
  all-REST-401 outage, the designated diagnostic answered ``compatible: true``.

* ``list_namespaces`` returned an empty envelope on a vCenter with no Workload
  Management, because ``/api/vcenter/namespaces/instances`` genuinely answers
  ``200 []`` there. Three sibling tools raise a teaching error for that state;
  this one read as "no namespaces here".

The tests below pin that the two states are distinguishable, not that any
particular wording survives.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from vmware_vks.errors import VksApiError
from vmware_vks.ops import namespace as ns_ops
from vmware_vks.ops import supervisor as sup


def _si(version="9.1.0"):
    return SimpleNamespace(
        content=SimpleNamespace(about=SimpleNamespace(version=version, build="123"))
    )


def test_a_rest_failure_is_not_reported_as_zero_clusters(monkeypatch):
    def _boom(_si, _path):
        raise VksApiError("401 Unauthorized")

    monkeypatch.setattr(sup, "_rest_get", _boom)
    out = sup.check_vks_compatibility(_si())

    assert out["wcp_query_failed"] is True
    assert out["workload_management_enabled"] is None, (
        "an unknown was reported as a definite 'no'"
    )
    assert out["wcp_enabled_clusters"] is None
    assert "401" in (out["wcp_query_error"] or "")
    assert out["hint"], "a failed query with no hint is the original bug"


def test_a_healthy_vcenter_with_no_supervisor_is_a_different_answer(monkeypatch):
    monkeypatch.setattr(sup, "_rest_get", lambda _si, _p: [])
    out = sup.check_vks_compatibility(_si())

    assert out["wcp_query_failed"] is False
    assert out["workload_management_enabled"] is False
    assert out["wcp_enabled_clusters"] == 0


def test_the_two_answers_are_not_byte_identical(monkeypatch):
    """The exact experiment from the field report."""
    monkeypatch.setattr(sup, "_rest_get", lambda _si, _p: [])
    healthy = sup.check_vks_compatibility(_si())

    def _boom(_si, _path):
        raise VksApiError("401 Unauthorized")

    monkeypatch.setattr(sup, "_rest_get", _boom)
    broken = sup.check_vks_compatibility(_si())

    assert healthy != broken


def test_a_running_supervisor_reports_enabled(monkeypatch):
    monkeypatch.setattr(
        sup, "_rest_get", lambda _si, _p: [{"cluster": "domain-c9", "config_status": "RUNNING"}]
    )
    out = sup.check_vks_compatibility(_si())
    assert out["workload_management_enabled"] is True
    assert out["wcp_enabled_clusters"] == 1
    assert out["hint"] is None, "a working Supervisor should not be hinted at"


def test_an_old_vcenter_still_says_so_first(monkeypatch):
    monkeypatch.setattr(sup, "_rest_get", lambda _si, _p: [])
    out = sup.check_vks_compatibility(_si(version="7.0.3"))
    assert out["version_compatible"] is False
    assert "8.0" in out["hint"]


def test_empty_namespace_list_says_whether_a_supervisor_exists(monkeypatch):
    monkeypatch.setattr(ns_ops, "_rest_get", lambda _si, _p: [])
    monkeypatch.setattr(
        sup,
        "check_vks_compatibility",
        lambda _si: {"wcp_query_failed": False, "workload_management_enabled": False},
    )
    out = ns_ops.list_namespaces(_si())

    assert out["returned"] == 0
    assert "Workload Management" in out["empty_reason"]


def test_empty_namespace_list_on_a_real_supervisor_says_so_too(monkeypatch):
    monkeypatch.setattr(ns_ops, "_rest_get", lambda _si, _p: [])
    monkeypatch.setattr(
        sup,
        "check_vks_compatibility",
        lambda _si: {"wcp_query_failed": False, "workload_management_enabled": True},
    )
    out = ns_ops.list_namespaces(_si())
    assert "no Supervisor" not in out["empty_reason"]


def test_a_nonempty_namespace_list_is_not_annotated(monkeypatch):
    monkeypatch.setattr(
        ns_ops, "_rest_get", lambda _si, _p: [{"namespace": "ns1", "config_status": "RUNNING"}]
    )
    out = ns_ops.list_namespaces(_si())
    assert "empty_reason" not in out


def test_the_probe_failing_does_not_break_the_listing(monkeypatch):
    """A diagnostic that can take down the thing it annotates is not worth having."""
    monkeypatch.setattr(ns_ops, "_rest_get", lambda _si, _p: [])

    def _boom(_si):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(sup, "check_vks_compatibility", _boom)
    out = ns_ops.list_namespaces(_si())
    assert out["returned"] == 0
    assert "RuntimeError" in out["empty_reason"]
