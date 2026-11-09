"""Cluster API v1beta2 contracts, without asserting live 9.1 compatibility."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import kubernetes as k8s
import pytest
import yaml

from vmware_vks.errors import VksApiError
from vmware_vks.ops import tkc
from vmware_vks.ops.cluster_status import readiness


@pytest.mark.parametrize("version", ["v1beta1", "v1beta2"])
def test_class_reference_and_drain_timeout_match_schema(version):
    raw = yaml.safe_load(tkc.generate_tkc_yaml(
        "app", "dev", "v1.33.1+vmware.1", "best-effort-large", 3, 2, "gold",
        api_version=version, cluster_class="builtin-generic-v3.6.0",
        cluster_class_namespace="vmware-system-vks-public",
    ))
    top = raw["spec"]["topology"]
    assert top["variables"] == [
        {"name": "vmClass", "value": "best-effort-large"},
        {"name": "storageClass", "value": "gold"},
    ]
    if version == "v1beta2":
        assert top["classRef"] == {"name": "builtin-generic-v3.6.0",
                                    "namespace": "vmware-system-vks-public"}
        assert "class" not in top and "classNamespace" not in top
    else:
        assert top["class"] == "builtin-generic-v3.6.0"
        assert top["classNamespace"] == "vmware-system-vks-public"
        assert "classRef" not in top
    for nodes in [top["controlPlane"], *top["workers"]["machineDeployments"]]:
        if version == "v1beta2":
            assert nodes["deletion"] == {"nodeDrainTimeoutSeconds": 60}
            assert "nodeDrainTimeout" not in nodes
            assert "metadata" not in nodes
        else:
            assert nodes["nodeDrainTimeout"] == "60s"
            assert "deletion" not in nodes


def test_modern_template_requires_explicit_class():
    with pytest.raises(ValueError, match="cluster_class"):
        tkc.generate_tkc_yaml("app", "dev", "v1.33", "large", 1, 1, "gold",
                              api_version="v1beta2")


def test_v1beta2_only_discovery_is_supported(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr("vmware_vks.k8s_connection.get_k8s_client", lambda *a: client)
    probe = MagicMock()
    probe.get_api_versions.return_value = SimpleNamespace(groups=[SimpleNamespace(
        name="cluster.x-k8s.io", versions=[SimpleNamespace(version="v1beta2")])])
    monkeypatch.setattr(k8s.client, "ApisApi", lambda c: probe)
    assert tkc._resolve_tkc_version(object(), "dev") == "v1beta2"
    client.close.assert_called_once()


@pytest.mark.parametrize("reject", [False, True])
def test_apply_requires_strict_nonpersisting_admission(monkeypatch, reject):
    api = MagicMock()
    monkeypatch.setattr(tkc, "_resolve_tkc_version", lambda *a: "v1beta2")
    monkeypatch.setattr(tkc, "_get_custom_objects_api", lambda *a: api)
    if reject:
        api.create_namespaced_custom_object.side_effect = k8s.client.exceptions.ApiException(
            status=422, reason="incompatible variables")
        monkeypatch.setattr(tkc, "_translate_api_exception", lambda *a, **k: VksApiError(
            "Supervisor admission rejected the class or variables", 422))
    kwargs = dict(si=object(), name="app", namespace="dev", k8s_version="v1.33",
                  vm_class="large", dry_run=False, cluster_class="builtin-generic-v3.6.0",
                  cluster_class_namespace="vmware-system-vks-public")
    if reject:
        with pytest.raises(VksApiError):
            tkc.create_tkc_cluster(**kwargs)
    else:
        assert tkc.create_tkc_cluster(**kwargs)["status"] == "creating"
    calls = api.create_namespaced_custom_object.call_args_list
    assert calls[0].kwargs["dry_run"] == "All"
    assert calls[0].kwargs["field_validation"] == "Strict"
    assert calls[0].kwargs["version"] == "v1beta2"
    assert len(calls) == (1 if reject else 2)
    if not reject:
        assert "dry_run" not in calls[1].kwargs
        assert calls[0].kwargs["body"] == calls[1].kwargs["body"]
    assert api.api_client.close.call_count == (1 if reject else 2)


@pytest.mark.parametrize("value,expected", [("True", True), ("False", False), ("Unknown", None)])
def test_v1beta2_readiness_is_a_condition_not_a_missing_legacy_boolean(value, expected):
    status = {"conditions": [{"type": "InfrastructureReady", "status": value}]}
    assert readiness(status, "InfrastructureReady", "infrastructureReady") is expected
    assert readiness(status, "ControlPlaneAvailable", "controlPlaneReady") is None


@pytest.mark.parametrize("operation", ["list", "get", "scale", "upgrade", "delete"])
def test_v1beta2_resource_paths_preserve_existing_topology(monkeypatch, operation):
    api = MagicMock()
    topology = {
        "classRef": {"name": "installed-class", "namespace": "shared"},
        "version": "v1.33.1", "controlPlane": {"replicas": 3},
        "workers": {"machineDeployments": [
            {"name": "one", "class": "custom-pool", "replicas": 2},
            {"name": "two", "class": "other-pool", "replicas": 4},
        ]},
    }
    raw = {"metadata": {"name": "app", "namespace": "dev"},
           "spec": {"topology": topology}, "status": {"conditions": [
               {"type": "InfrastructureReady", "status": "True"},
               {"type": "ControlPlaneAvailable", "status": "Unknown"},
           ]}}
    api.get_namespaced_custom_object.return_value = raw
    api.list_namespaced_custom_object.return_value = {"items": [raw]}
    monkeypatch.setattr(tkc, "_resolve_tkc_version", lambda *a: "v1beta2")
    monkeypatch.setattr(tkc, "_get_custom_objects_api", lambda *a: api)
    if operation == "list":
        assert tkc.list_tkc_clusters(object(), "dev")["total"] == 1
        call = api.list_namespaced_custom_object.call_args
    elif operation == "get":
        out = tkc.get_tkc_cluster(object(), "app", "dev")
        assert out["infrastructure_ready"] is True and out["control_plane_ready"] is None
        assert out["worker_replica_total"] == 6
        call = api.get_namespaced_custom_object.call_args
    elif operation == "scale":
        tkc.scale_tkc_cluster(object(), "app", "dev", 5, pool_name="two")
        call = api.patch_namespaced_custom_object.call_args
        pools = call.kwargs["body"]["spec"]["topology"]["workers"]["machineDeployments"]
        assert pools == [{"name": "one", "class": "custom-pool", "replicas": 2},
                         {"name": "two", "class": "other-pool", "replicas": 5}]
        assert topology["workers"]["machineDeployments"][1]["replicas"] == 4
    elif operation == "upgrade":
        tkc.upgrade_tkc_cluster(object(), "app", "dev", "v1.34.1")
        call = api.patch_namespaced_custom_object.call_args
        assert call.kwargs["body"] == {"spec": {"topology": {"version": "v1.34.1"}}}
    else:
        tkc.delete_tkc_cluster(object(), "app", "dev", confirmed=True, dry_run=False, force=True)
        call = api.delete_namespaced_custom_object.call_args
    assert call.kwargs["version"] == "v1beta2"
    api.api_client.close.assert_called_once()


@pytest.mark.parametrize("confirm", [False, True])
@pytest.mark.parametrize("reject", [False, True])
def test_mcp_modern_class_reaches_admission_before_any_persistent_create(
    monkeypatch, confirm, reject
):
    from vmware_vks.mcp_server import server

    api = MagicMock()
    monkeypatch.setattr(server, "_get_si", lambda *a: object())
    monkeypatch.setattr(tkc, "_resolve_tkc_version", lambda *a: "v1beta2")
    monkeypatch.setattr(tkc, "_get_custom_objects_api", lambda *a: api)
    monkeypatch.setattr(tkc, "list_tkc_clusters", lambda *a, **k: {"items": []})
    if reject:
        api.create_namespaced_custom_object.side_effect = k8s.client.exceptions.ApiException(
            status=422, reason="incompatible class")
        monkeypatch.setattr(tkc, "_translate_api_exception", lambda *a, **k: VksApiError(
            "Supervisor admission rejected the class or variables", 422))
    result = server.create_tkc_cluster(
        name="app", namespace="dev", k8s_version="v1.33.1", vm_class="large",
        cluster_class="builtin-generic-v3.6.0", cluster_class_namespace="shared",
        confirm=confirm, target="lab-vc",
    )
    calls = api.create_namespaced_custom_object.call_args_list
    assert calls[0].kwargs["body"]["spec"]["topology"]["classRef"] == {
        "name": "builtin-generic-v3.6.0", "namespace": "shared"}
    assert calls[0].kwargs["dry_run"] == "All"
    persisted = [c for c in calls if c.kwargs.get("dry_run") != "All"]
    if reject:
        assert "error" in result
        assert not persisted and len(calls) == 1
    else:
        assert result["action"] == ("created" if confirm else "preview")
        assert len(persisted) == int(confirm)
        assert result["blast_radius"]["admission_validated"] is True
