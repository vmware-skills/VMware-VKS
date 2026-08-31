"""VM Service (vm-operator) read tools touch only spec-listed CRD paths.

Anti-phantom-endpoint guard (CLAUDE.md 踩坑 #36): a prior skill shipped
hallucinated endpoints, half of which 404'd. ``tests/eval/spec/
vmservice_endpoints.py`` pins the exact vmoperator.vmware.com group, plurals,
kinds and minimum versions. These tests assert the ops module
(``vmware_vks.ops.vmservice``) declares nothing outside that spec AND that no
``group=``/``plural=`` literal in its source escapes the pinned set — so the
CRD surface cannot silently drift into an invented path.

They also cover the runtime version discovery (newest served among
v1alpha4/5/6, gated by each kind's minimum), the family list envelope, and the
defensive field access that lets an absent field degrade to empty rather than
crash (踩坑 形态 #1).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.eval.spec import vmservice_endpoints as spec
from vmware_vks.errors import VksApiError
from vmware_vks.ops import vmservice as vmsvc

_OPS_SOURCE = Path(vmsvc.__file__).read_text(encoding="utf-8")

ENVELOPE_KEYS = {"items", "returned", "limit", "total", "truncated", "hint"}


def _mock_si() -> MagicMock:
    si = MagicMock()
    si._stub.host = "vcenter.example.com"
    return si


@pytest.fixture(autouse=True)
def _clear_cache():
    """Each test starts with an empty served-version cache."""
    vmsvc._served_cache.clear()
    yield
    vmsvc._served_cache.clear()


# ---------------------------------------------------------------------------
# Anti-phantom: the module's declared surface is a subset of the spec
# ---------------------------------------------------------------------------


def test_group_matches_spec():
    assert vmsvc._VMOP_GROUP == spec.VMOP_GROUP


def test_versions_are_subset_of_spec():
    assert set(vmsvc._VMOP_VERSIONS) <= set(spec.VMOP_VERSIONS)
    # newest-first ordering must match so _pick_version prefers the newest
    assert vmsvc._VMOP_VERSIONS == spec.VMOP_VERSIONS


def test_plurals_are_spec_listed():
    assert set(vmsvc._RESOURCES) <= spec.ALLOWED_PLURALS


def test_min_versions_match_spec():
    for plural, (kind, min_version) in vmsvc._RESOURCES.items():
        assert spec.RESOURCES[plural]["kind"] == kind
        assert spec.RESOURCES[plural]["min_version"] == min_version


def test_source_group_literals_are_spec_group():
    """Every ``group="..."`` literal in the ops source is the spec group."""
    groups = set(re.findall(r'group=["\']([^"\']+)["\']', _OPS_SOURCE))
    # keyword-arg form uses the constant, but guard against a hardcoded string
    for g in groups:
        assert g == spec.VMOP_GROUP, f"phantom group literal: {g}"


def test_source_plural_literals_are_spec_listed():
    """Every ``plural="..."`` literal in the ops source is spec-listed."""
    plurals = set(re.findall(r'plural=["\']([^"\']+)["\']', _OPS_SOURCE))
    for p in plurals:
        assert p in spec.ALLOWED_PLURALS, f"phantom plural literal: {p}"


def test_no_container_service_tool():
    """Container Service has no CRD (spec section D) — no plural for it."""
    assert "container" not in " ".join(spec.ALLOWED_PLURALS).lower()
    for feature in spec.NO_CRD_FEATURES:
        assert feature.replace("-", "") not in _OPS_SOURCE.lower().replace("-", "")


def test_ast_customobject_calls_use_the_group_constant():
    """CustomObjectsApi calls pass the pinned group constant, not a literal."""
    tree = ast.parse(_OPS_SOURCE)
    call_names = {"list_namespaced_custom_object", "get_namespaced_custom_object"}
    seen = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in call_names):
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords}
        group = kwargs.get("group")
        assert isinstance(group, ast.Name) and group.id == "_VMOP_GROUP", (
            "CRD call must pass group=_VMOP_GROUP (the spec-pinned constant)"
        )
        seen += 1
    assert seen >= 3, "expected the three CRD read calls"


# ---------------------------------------------------------------------------
# Runtime version discovery
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "served, min_version, expected",
    [
        (frozenset({"v1alpha4", "v1alpha5", "v1alpha6"}), "v1alpha5", "v1alpha6"),
        (frozenset({"v1alpha4", "v1alpha5"}), "v1alpha5", "v1alpha5"),
        (frozenset({"v1alpha4"}), "v1alpha5", None),
        (frozenset({"v1alpha1", "v1alpha2"}), "v1alpha1", "v1alpha2"),
        (frozenset(), "v1alpha1", None),
    ],
)
def test_pick_version_prefers_newest_at_or_above_min(served, min_version, expected):
    assert vmsvc._pick_version(served, min_version) == expected


def test_resolve_version_raises_teaching_error_when_kind_absent():
    """A Supervisor too old for the snapshot CRD gets a version-named error."""
    with patch.object(
        vmsvc, "_discover_served_versions",
        return_value=frozenset({"v1alpha3", "v1alpha4"}),
    ):
        with pytest.raises(VksApiError) as ei:
            vmsvc._resolve_version(_mock_si(), "dev", vmsvc._PLURAL_SNAPSHOT)
    msg = str(ei.value)
    assert "v1alpha5" in msg  # names the required version
    assert "VirtualMachineSnapshot" in msg


# ---------------------------------------------------------------------------
# List tools — envelope + defensive field access
# ---------------------------------------------------------------------------


def _api_returning(items: list[dict]) -> MagicMock:
    api = MagicMock()
    api.list_namespaced_custom_object.return_value = {"items": items}
    return api


def _run_list(fn, items, **kwargs):
    api = _api_returning(items)
    with (
        patch.object(vmsvc, "_get_custom_objects_api", return_value=api),
        patch.object(vmsvc, "_resolve_version", return_value="v1alpha5"),
    ):
        return fn(_mock_si(), "dev", **kwargs)


def test_snapshot_list_envelope_and_fields():
    items = [
        {
            "metadata": {"name": "snap-1", "namespace": "dev", "creationTimestamp": "t0"},
            "spec": {"vmName": "web-01"},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }
    ]
    result = _run_list(vmsvc.list_vm_snapshots, items)
    assert ENVELOPE_KEYS <= set(result)
    assert result["served_version"] == "v1alpha5"
    row = result["items"][0]
    assert row["name"] == "snap-1"
    assert row["vm_name"] == "web-01"
    assert row["ready"] == "True"


def test_snapshot_list_missing_fields_degrade_to_empty():
    """An item stripped to bare metadata must not crash (踩坑 形态 #1)."""
    result = _run_list(vmsvc.list_vm_snapshots, [{"metadata": {}}])
    row = result["items"][0]
    assert row["name"] == ""
    assert row["vm_name"] == ""
    assert row["ready"] is None
    assert result["truncated"] is False


def test_group_list_bootorder_and_member_count():
    items = [
        {
            "metadata": {"name": "grp-1", "namespace": "dev"},
            "spec": {
                "bootOrder": [
                    {"members": [{"kind": "VirtualMachine", "name": "db-01"}]},
                    {
                        "members": [
                            {"kind": "VirtualMachine", "name": "web-01"},
                            {"kind": "VirtualMachine", "name": "web-02"},
                        ],
                        "powerOnDelay": "10s",
                    },
                ]
            },
        }
    ]
    result = _run_list(vmsvc.list_vm_groups, items)
    row = result["items"][0]
    assert row["member_count"] == 3
    assert len(row["boot_order"]) == 2
    assert row["boot_order"][0]["members"][0]["name"] == "db-01"
    assert row["boot_order"][1]["power_on_delay"] == "10s"


def test_group_list_missing_bootorder_is_empty():
    result = _run_list(vmsvc.list_vm_groups, [{"metadata": {"name": "g"}}])
    row = result["items"][0]
    assert row["boot_order"] == []
    assert row["member_count"] == 0


def test_empty_collection_is_explicit_zero():
    result = _run_list(vmsvc.list_vm_snapshots, [])
    assert result["items"] == []
    assert result["total"] == 0
    assert result["truncated"] is False


# ---------------------------------------------------------------------------
# Network interfaces — single VM multi-NIC readout
# ---------------------------------------------------------------------------


def _run_nics(vm: dict):
    api = MagicMock()
    api.get_namespaced_custom_object.return_value = vm
    with (
        patch.object(vmsvc, "_get_custom_objects_api", return_value=api),
        patch.object(vmsvc, "_resolve_version", return_value="v1alpha4"),
    ):
        return vmsvc.list_vm_network_interfaces(_mock_si(), "dev", "web-01")


def test_network_interfaces_multi_nic():
    vm = {
        "spec": {
            "network": {
                "interfaces": [
                    {"name": "eth0", "network": {"name": "primary", "kind": "Network"}},
                    {"name": "eth1", "network": {"name": "storage", "kind": "VpcNetwork"}},
                ]
            }
        }
    }
    result = _run_nics(vm)
    assert ENVELOPE_KEYS <= set(result)
    assert result["vm_name"] == "web-01"
    assert result["total"] == 2
    assert result["items"][1]["network_name"] == "storage"
    assert result["items"][1]["network_kind"] == "VpcNetwork"


def test_network_interfaces_no_network_block_is_empty():
    """A VM without spec.network degrades to an empty NIC list, not an error."""
    result = _run_nics({"spec": {}})
    assert result["items"] == []
    assert result["total"] == 0
    assert result["truncated"] is False


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------


def test_vmservice_tools_are_registered():
    import asyncio

    import vmware_vks.mcp_server.server as srv

    names = {t.name for t in asyncio.run(srv.mcp.list_tools())}
    assert {"list_vm_snapshots", "list_vm_groups", "list_vm_network_interfaces"} <= names
