"""The confirmation gate on the four VKS write tools (HLD §7, revised 2026-09-16).

Contract per tool:

* ``confirm: bool = False`` — a bare call previews and calls no write API (L2);
* preview and acting response both carry ``blast_radius`` (L1);
* ``confirm=True`` is refused on a blocker or an unmeasured field (L3);
* the legacy ``confirmed`` / ``dry_run`` spellings are deprecated aliases, the
  conservative reading wins, and a response that used one says so.

The write APIs are the real ops-layer functions (``_rest_post`` /
``_rest_delete`` and the Kubernetes ``CustomObjectsApi`` create/delete), so a
tool that reaches them on the preview path fails here.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from vmware_vks.errors import VksApiError, VksSafetyError
from vmware_vks.mcp_server import server as srv

NS = "dev"
CLUSTER = "tkc-01"


# ---------------------------------------------------------------------------
# Fixtures: a fake Supervisor whose reads answer and whose writes are recorded
# ---------------------------------------------------------------------------


class _Env:
    """Patch the ops layer's reads and writes; expose the write mocks."""

    def __init__(self) -> None:
        self.rest_post = MagicMock(return_value=None)
        self.rest_delete = MagicMock(return_value=None)
        self.api = MagicMock()
        self.api.get_namespaced_custom_object.return_value = {
            "metadata": {"name": CLUSTER, "namespace": NS},
            "spec": {"topology": {
                "version": "v1.28.4",
                "controlPlane": {"replicas": 3},
                "workers": {"machineDeployments": [
                    {"name": "pool-a", "replicas": 2},
                    {"name": "pool-b", "replicas": 4},
                ]},
            }},
            "status": {"phase": "Provisioned"},
        }
        self.tkc_items = [{"name": "c1"}, {"name": "c2"}]
        self.vms = ["vm-a", "vm-b", "vm-c"]
        self.pvcs = [{"name": "pvc-1", "capacity": "10Gi"}]
        self.namespaces = [{"namespace": "other"}]
        self.workloads: list[dict] = []
        self.tkc_error: Exception | None = None
        self.vm_error: Exception | None = None
        self.pvc_error: Exception | None = None
        self.workload_error: Exception | None = None
        self.list_error: Exception | None = None

    # Reads ---------------------------------------------------------------
    def rest_get(self, si, path):
        if path == "/vcenter/namespaces/instances":
            if self.list_error:
                raise self.list_error
            return self.namespaces
        if path == f"/vcenter/namespaces/instances/{NS}":
            return {"config_status": "RUNNING"}
        raise AssertionError(f"unexpected GET {path}")

    def list_tkc(self, si, namespace=None):
        if self.tkc_error:
            raise self.tkc_error
        return {"items": [{**c, "namespace": namespace} for c in self.tkc_items]}

    def list_vms(self, si, namespace):
        if self.vm_error:
            raise self.vm_error
        return {"items": list(self.vms), "vm_service": "installed"}

    def list_pvcs(self, si, namespace):
        if self.pvc_error:
            raise self.pvc_error
        return {"items": list(self.pvcs)}

    def check_workloads(self, si, name, namespace):
        if self.workload_error:
            raise self.workload_error
        return list(self.workloads)


@pytest.fixture
def env():
    e = _Env()
    with ExitStack() as stack:
        p = stack.enter_context
        p(patch.object(srv, "_get_si", lambda target=None: MagicMock()))
        p(patch.object(srv._audit, "log", lambda **kw: None))
        p(patch("vmware_vks.ops.namespace._rest_get", side_effect=e.rest_get))
        p(patch("vmware_vks.ops.namespace._rest_post", e.rest_post))
        p(patch("vmware_vks.ops.namespace._rest_delete", e.rest_delete))
        p(patch("vmware_vks.ops.tkc.list_tkc_clusters", side_effect=e.list_tkc))
        p(patch("vmware_vks.ops.vmservice.list_vm_names", side_effect=e.list_vms))
        p(patch("vmware_vks.ops.storage.list_namespace_storage_usage", side_effect=e.list_pvcs))
        p(patch("vmware_vks.ops.tkc._check_running_workloads", side_effect=e.check_workloads))
        p(patch("vmware_vks.ops.tkc._resolve_tkc_version", return_value="v1beta1"))
        p(patch("vmware_vks.ops.tkc._get_custom_objects_api", return_value=e.api))
        yield e


def _no_writes(env: _Env) -> None:
    env.rest_post.assert_not_called()
    env.rest_delete.assert_not_called()
    env.api.create_namespaced_custom_object.assert_not_called()
    env.api.delete_namespaced_custom_object.assert_not_called()


def _create_ns(**kw):
    return srv.create_namespace(name=NS, cluster_id="domain-c9", storage_policy="gold", **kw)


def _create_tkc(**kw):
    return srv.create_tkc_cluster(
        name=CLUSTER, namespace=NS, k8s_version="v1.28.4", vm_class="best-effort-small",
        control_plane_count=3, worker_count=2, **kw,
    )


# ---------------------------------------------------------------------------
# Schema: confirm defaults to False; the aliases default to None
# ---------------------------------------------------------------------------

GATED = ("delete_namespace", "delete_tkc_cluster", "create_namespace", "create_tkc_cluster")


@pytest.mark.parametrize("tool", GATED)
def test_schema_confirm_defaults_to_false_and_aliases_to_none(tool):
    tools = {t.name: t for t in asyncio.run(srv.mcp.list_tools())}
    props = tools[tool].inputSchema["properties"]
    assert props["confirm"]["default"] is False
    assert props["dry_run"].get("default", "missing") is None
    if tool.startswith("delete"):
        assert props["confirmed"].get("default", "missing") is None
    assert props["confirm"]["description"].startswith(
        "False (default) returns the blast radius and changes nothing. True applies it."
    )
    assert props["dry_run"]["description"].startswith("Deprecated alias for confirm")
    assert "Do not set\nconfirm=True on your own" in tools[tool].description or (
        "Do not set confirm=True on your own" in " ".join(tools[tool].description.split())
    )


# ---------------------------------------------------------------------------
# delete_namespace
# ---------------------------------------------------------------------------


def test_delete_namespace_bare_call_previews_with_measured_radius(env):
    env.tkc_items = []
    result = srv.delete_namespace(name=NS)
    assert result["action"] == "preview"
    radius = result["blast_radius"]
    assert radius["namespace"] == NS
    assert radius["config_status"] == "RUNNING"
    assert radius["tkc_cluster_count"] == 0
    assert radius["vm_count"] == 3 and radius["vms"] == ["vm-a", "vm-b", "vm-c"]
    assert radius["pvc_count"] == 1 and radius["pvcs"][0]["name"] == "pvc-1"
    assert radius["blockers"] == [] and radius["unmeasured"] == []
    assert "deprecated" not in result
    _no_writes(env)


def test_delete_namespace_confirm_deletes_once_and_returns_radius(env):
    env.tkc_items = []
    result = srv.delete_namespace(name=NS, confirm=True)
    assert result["action"] == "deleted"
    assert result["blast_radius"]["vm_count"] == 3
    env.rest_delete.assert_called_once()
    assert env.rest_delete.call_args.args[1] == f"/vcenter/namespaces/instances/{NS}"


def test_delete_namespace_tkc_inside_is_a_blocker(env):
    preview = srv.delete_namespace(name=NS)
    assert preview["blast_radius"]["tkc_cluster_count"] == 2
    assert preview["blast_radius"]["blockers"]
    result = srv.delete_namespace(name=NS, confirm=True)
    assert "error" in result and "delete_tkc_cluster" in result["error"]
    _no_writes(env)


@pytest.mark.parametrize("field", ["tkc_error", "vm_error", "pvc_error"])
def test_delete_namespace_unreadable_field_refuses(env, field):
    env.tkc_items = []
    setattr(env, field, VksApiError("boom", 503))
    preview = srv.delete_namespace(name=NS)
    assert preview["action"] == "preview"
    assert preview["blast_radius"]["unmeasured"]
    result = srv.delete_namespace(name=NS, confirm=True)
    assert "error" in result and "could not read" in result["error"].lower()
    _no_writes(env)


def test_delete_namespace_vm_service_absent_is_zero_not_unmeasured(env):
    env.tkc_items = []
    env.vms = []
    result = srv.delete_namespace(name=NS)
    assert result["blast_radius"]["vm_count"] == 0
    assert result["blast_radius"]["unmeasured"] == []


# Alias table: (kwargs, acts?) — the conservative reading wins.
DELETE_ALIASES = [
    ({"confirmed": True, "dry_run": False}, True),   # the old acting call
    ({"confirmed": True}, False),                    # old dry_run default was True
    ({"dry_run": False}, False),                     # old contract also needed confirmed
    ({"confirmed": False, "dry_run": True}, False),
    ({"confirm": True, "dry_run": True}, False),     # disagree -> preview
    ({"confirm": True, "confirmed": False}, False),  # disagree -> preview
    ({"confirm": True, "confirmed": True, "dry_run": False}, True),
    ({"confirm": True, "dry_run": False}, True),
]


@pytest.mark.parametrize("kwargs,acts", DELETE_ALIASES)
def test_delete_namespace_aliases(env, kwargs, acts):
    env.tkc_items = []
    result = srv.delete_namespace(name=NS, **kwargs)
    assert result["action"] == ("deleted" if acts else "preview")
    assert env.rest_delete.call_count == (1 if acts else 0)
    used = [k for k in ("confirmed", "dry_run") if k in kwargs]
    if used:
        assert "deprecated; use confirm" in result["deprecated"]
        for name in used:
            assert name in result["deprecated"]
    else:
        assert "deprecated" not in result


def test_delete_namespace_refusal_is_audited_as_failure(env, monkeypatch):
    rows = []

    class _Recorder:
        def log(self, **kw):
            rows.append(kw)

    monkeypatch.setattr("vmware_policy.guard.get_engine", lambda: _Recorder())
    result = srv.delete_namespace(name=NS, confirm=True)
    assert "error" in result
    assert rows and rows[-1]["status"] == "error"


# ---------------------------------------------------------------------------
# delete_tkc_cluster
# ---------------------------------------------------------------------------


def test_delete_tkc_bare_call_previews_with_node_counts(env):
    result = srv.delete_tkc_cluster(name=CLUSTER, namespace=NS)
    assert result["action"] == "preview"
    radius = result["blast_radius"]
    assert radius["cluster"] == CLUSTER and radius["namespace"] == NS
    assert radius["control_plane_nodes"] == 3
    assert radius["worker_nodes"] == 6
    assert radius["node_count"] == 9
    assert radius["worker_pools"] == [
        {"name": "pool-a", "replicas": 2}, {"name": "pool-b", "replicas": 4},
    ]
    assert radius["workload_count"] == 0
    assert radius["blockers"] == [] and radius["unmeasured"] == []
    _no_writes(env)


def test_delete_tkc_confirm_deletes_once(env):
    result = srv.delete_tkc_cluster(name=CLUSTER, namespace=NS, confirm=True)
    assert result["action"] == "deleted"
    assert result["blast_radius"]["node_count"] == 9
    env.api.delete_namespaced_custom_object.assert_called_once()


def test_delete_tkc_running_workloads_block(env):
    env.workloads = [{"kind": "Deployment", "name": "web", "namespace": "default"}]
    preview = srv.delete_tkc_cluster(name=CLUSTER, namespace=NS)
    assert preview["blast_radius"]["workload_count"] == 1
    assert preview["blast_radius"]["blockers"]
    result = srv.delete_tkc_cluster(name=CLUSTER, namespace=NS, confirm=True)
    assert "error" in result and "running workload" in result["error"]
    _no_writes(env)


def test_delete_tkc_unreadable_workloads_refuse(env):
    env.workload_error = VksSafetyError("Cannot verify workloads")
    preview = srv.delete_tkc_cluster(name=CLUSTER, namespace=NS)
    assert "workloads" in preview["blast_radius"]["unmeasured"]
    result = srv.delete_tkc_cluster(name=CLUSTER, namespace=NS, confirm=True)
    assert "error" in result and "could not read" in result["error"].lower()
    _no_writes(env)


def test_delete_tkc_unreadable_cluster_object_refuses(env):
    env.api.get_namespaced_custom_object.side_effect = TypeError("bad payload")
    preview = srv.delete_tkc_cluster(name=CLUSTER, namespace=NS)
    assert "node_counts" in preview["blast_radius"]["unmeasured"]
    result = srv.delete_tkc_cluster(name=CLUSTER, namespace=NS, confirm=True)
    assert "error" in result
    _no_writes(env)


def test_delete_tkc_force_skips_workload_check_and_deletes(env):
    env.workloads = [{"kind": "Deployment", "name": "web", "namespace": "default"}]
    result = srv.delete_tkc_cluster(name=CLUSTER, namespace=NS, force=True, confirm=True)
    assert result["action"] == "deleted"
    assert result["blast_radius"]["workloads"] is None
    assert result["blast_radius"]["skipped"]
    env.api.delete_namespaced_custom_object.assert_called_once()


@pytest.mark.parametrize("kwargs,acts", DELETE_ALIASES)
def test_delete_tkc_aliases(env, kwargs, acts):
    result = srv.delete_tkc_cluster(name=CLUSTER, namespace=NS, **kwargs)
    assert result["action"] == ("deleted" if acts else "preview")
    assert env.api.delete_namespaced_custom_object.call_count == (1 if acts else 0)
    if any(k in kwargs for k in ("confirmed", "dry_run")):
        assert "deprecated; use confirm" in result["deprecated"]


# ---------------------------------------------------------------------------
# create_namespace
# ---------------------------------------------------------------------------


def test_create_namespace_bare_call_previews(env):
    result = _create_ns(cpu_limit=4000)
    assert result["action"] == "preview"
    radius = result["blast_radius"]
    assert radius["namespace"] == NS
    assert radius["cluster_id"] == "domain-c9"
    assert radius["storage_policy"] == "gold"
    assert radius["spec"]["resource_spec"] == {"cpu_limit": 4000}
    assert radius["name_taken"] is False
    assert radius["blockers"] == [] and radius["unmeasured"] == []
    _no_writes(env)


def test_create_namespace_confirm_creates_once(env):
    result = _create_ns(confirm=True)
    assert result["action"] == "created"
    assert result["blast_radius"]["namespace"] == NS
    env.rest_post.assert_called_once()


def test_create_namespace_existing_name_blocks(env):
    env.namespaces = [{"namespace": NS}]
    assert _create_ns()["blast_radius"]["blockers"]
    result = _create_ns(confirm=True)
    assert "error" in result and "update_namespace" in result["error"]
    _no_writes(env)


def test_create_namespace_unreadable_list_refuses(env):
    env.list_error = VksApiError("boom", 503)
    assert _create_ns()["blast_radius"]["unmeasured"] == ["existing_namespaces"]
    result = _create_ns(confirm=True)
    assert "error" in result
    _no_writes(env)


CREATE_ALIASES = [
    ({"dry_run": False}, True),
    ({"dry_run": True}, False),
    ({"confirm": True, "dry_run": True}, False),
    ({"confirm": True, "dry_run": False}, True),
]


@pytest.mark.parametrize("kwargs,acts", CREATE_ALIASES)
def test_create_namespace_aliases(env, kwargs, acts):
    result = _create_ns(**kwargs)
    assert result["action"] == ("created" if acts else "preview")
    assert env.rest_post.call_count == (1 if acts else 0)
    assert "dry_run is deprecated; use confirm" in result["deprecated"]


def test_create_namespace_undo_only_after_a_real_create(env, monkeypatch):
    recorded = []

    class _Store:
        def record(self, **kw):
            recorded.append(kw)
            return "undo-1"

    monkeypatch.setattr("vmware_policy.undo.get_undo_store", lambda: _Store())
    _create_ns()
    assert recorded == [], "a preview recorded an undo token for a namespace never created"
    _create_ns(confirm=True)
    assert len(recorded) == 1
    assert recorded[0]["undo_descriptor"]["params"]["confirm"] is True


# ---------------------------------------------------------------------------
# create_tkc_cluster
# ---------------------------------------------------------------------------


def test_create_tkc_bare_call_previews(env):
    env.tkc_items = [{"name": "other"}]
    result = _create_tkc()
    assert result["action"] == "preview"
    radius = result["blast_radius"]
    assert radius["cluster"] == CLUSTER and radius["namespace"] == NS
    assert radius["control_plane_nodes"] == 3 and radius["worker_nodes"] == 2
    assert radius["node_count"] == 5
    assert radius["api_version"] == "v1beta1"
    assert "kind: Cluster" in radius["yaml"]
    assert radius["blockers"] == [] and radius["unmeasured"] == []
    _no_writes(env)


def test_create_tkc_confirm_creates_once(env):
    env.tkc_items = []
    result = _create_tkc(confirm=True)
    assert result["action"] == "created"
    assert result["blast_radius"]["node_count"] == 5
    env.api.create_namespaced_custom_object.assert_called_once()


def test_create_tkc_existing_name_blocks(env):
    env.tkc_items = [{"name": CLUSTER}]
    assert _create_tkc()["blast_radius"]["blockers"]
    result = _create_tkc(confirm=True)
    assert "error" in result and "already exists" in result["error"]
    _no_writes(env)


def test_create_tkc_unreadable_cluster_list_refuses(env):
    env.tkc_error = VksApiError("namespace not found", 404)
    assert _create_tkc()["blast_radius"]["unmeasured"] == ["existing_clusters"]
    result = _create_tkc(confirm=True)
    assert "error" in result
    _no_writes(env)


@pytest.mark.parametrize("kwargs,acts", CREATE_ALIASES)
def test_create_tkc_aliases(env, kwargs, acts):
    env.tkc_items = []
    result = _create_tkc(**kwargs)
    assert result["action"] == ("created" if acts else "preview")
    assert env.api.create_namespaced_custom_object.call_count == (1 if acts else 0)
    assert "dry_run is deprecated; use confirm" in result["deprecated"]


# ---------------------------------------------------------------------------
# The two reads added for the blast radius
# ---------------------------------------------------------------------------


def test_list_vm_names_absent_vm_service_is_a_real_zero():
    from vmware_vks.ops import vmservice as vmsvc

    with patch.object(vmsvc, "_discover_served_versions", return_value=frozenset()):
        assert vmsvc.list_vm_names(MagicMock(), NS) == {"items": [], "vm_service": "not installed"}


def test_list_vm_names_lists_every_vm():
    from vmware_vks.ops import vmservice as vmsvc

    api = MagicMock()
    api.list_namespaced_custom_object.return_value = {
        "items": [{"metadata": {"name": "a"}}, {"metadata": {"name": "b"}}],
        "metadata": {},
    }
    with patch.object(vmsvc, "_discover_served_versions", return_value=frozenset({"v1alpha3"})), \
         patch.object(vmsvc, "_get_custom_objects_api", return_value=api):
        result = vmsvc.list_vm_names(MagicMock(), NS)
    assert result == {"items": ["a", "b"], "vm_service": "installed"}
    kw = api.list_namespaced_custom_object.call_args.kwargs
    assert kw["plural"] == "virtualmachines" and kw["version"] == "v1alpha3"


def test_read_tkc_topology_reports_every_pool(env):
    from vmware_vks.ops import tkc

    topo = tkc.read_tkc_topology(MagicMock(), CLUSTER, NS)
    assert topo["control_plane_replicas"] == 3
    assert [p["replicas"] for p in topo["worker_pools"]] == [2, 4]


_MIN = "cluster.x-k8s.io/cluster-api-autoscaler-node-group-min-size"
_MAX = "cluster.x-k8s.io/cluster-api-autoscaler-node-group-max-size"


def _autoscaled_cluster(env, annotations):
    env.api.get_namespaced_custom_object.return_value = {
        "spec": {"topology": {"controlPlane": {"replicas": 1}, "workers": {"machineDeployments": [
            {"name": "fixed", "replicas": 2},
            {"name": "auto", "metadata": {"annotations": annotations}},
        ]}}},
    }


def test_an_autoscaled_pool_reports_its_range_and_does_not_block_delete(env):
    """A pool without replicas (cluster-autoscaler) is not a gap: delete removes every node."""
    _autoscaled_cluster(env, {_MIN: "1", _MAX: "5"})
    radius = srv.delete_tkc_cluster(name=CLUSTER, namespace=NS)["blast_radius"]
    assert radius["worker_nodes"] is None, "an unknown count must not read as a number"
    assert radius["worker_nodes_range"] == {"min": 3, "max": 7}
    assert radius["worker_pools"][1] == {
        "name": "auto", "replicas": None, "autoscaled": True, "min_size": 1, "max_size": 5,
    }
    assert radius["unmeasured"] == []
    result = srv.delete_tkc_cluster(name=CLUSTER, namespace=NS, confirm=True)
    assert result["action"] == "deleted"
    env.api.delete_namespaced_custom_object.assert_called_once()


def test_a_pool_without_replicas_or_range_is_unknown_but_not_a_gap(env):
    _autoscaled_cluster(env, {})
    radius = srv.delete_tkc_cluster(name=CLUSTER, namespace=NS)["blast_radius"]
    assert radius["worker_nodes"] is None and radius["worker_nodes_range"] is None
    assert radius["worker_pools"][1]["autoscaled"] is True
    assert radius["worker_pools"][1]["min_size"] is None
    assert "node_counts" not in radius["unmeasured"]


def test_autoscaled_cluster_still_refuses_when_workloads_unreadable(env):
    _autoscaled_cluster(env, {_MIN: "1", _MAX: "5"})
    env.workload_error = VksSafetyError("Cannot verify workloads")
    result = srv.delete_tkc_cluster(name=CLUSTER, namespace=NS, confirm=True)
    assert "error" in result and "force=True" in result["error"]
    _no_writes(env)
    forced = srv.delete_tkc_cluster(name=CLUSTER, namespace=NS, confirm=True, force=True)
    assert forced["action"] == "deleted"


def test_running_workloads_refusal_keeps_the_force_escape(env):
    env.workloads = [{"kind": "Deployment", "name": "web", "namespace": "default"}]
    result = srv.delete_tkc_cluster(name=CLUSTER, namespace=NS, confirm=True)
    assert "re-run delete_tkc_cluster with force=True" in result["error"]
    _no_writes(env)


def test_misspelt_cluster_name_gets_the_404_teaching_error(env):
    from kubernetes.client.exceptions import ApiException

    env.api.get_namespaced_custom_object.side_effect = ApiException(status=404, reason="Not Found")
    for kw in ({}, {"confirm": True}):
        result = srv.delete_tkc_cluster(name="tkc-typo", namespace=NS, **kw)
        assert "error" in result, result
        assert "not found" in result["error"] and "list_tkc_clusters" in result["error"]
    _no_writes(env)


def test_non_list_namespace_response_is_unmeasured(env):
    env.namespaces = {"unexpected": "shape"}
    radius = _create_ns()["blast_radius"]
    assert radius["name_taken"] is None
    assert radius["unmeasured"] == ["existing_namespaces"]
    assert "error" in _create_ns(confirm=True)
    _no_writes(env)


@pytest.mark.parametrize("call", [
    lambda: srv.delete_tkc_cluster(name=CLUSTER, namespace=NS, confirm=True),
    lambda: _create_ns(confirm=True),
])
def test_refusal_envelope_carries_blast_radius(env, call):
    env.workloads = [{"kind": "Deployment", "name": "web", "namespace": "default"}]
    env.namespaces = [{"namespace": NS}]
    result = call()
    assert "error" in result
    assert result["blast_radius"]["blockers"]
    _no_writes(env)
