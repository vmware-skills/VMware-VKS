"""Tests for TKC ops (unit, no real vCenter/K8s)."""
from unittest.mock import MagicMock, patch
import pytest
from vmware_vks.ops.tkc import (
    generate_tkc_yaml,
    list_tkc_clusters,
    get_tkc_cluster,
    delete_tkc_cluster,
)


def test_generate_tkc_yaml_contains_cluster_name():
    yaml_str = generate_tkc_yaml(
        name="my-cluster", namespace="dev",
        k8s_version="v1.28.4+vmware.1", vm_class="best-effort-large",
        control_plane_count=1, worker_count=3, storage_class="vsphere-storage",
    )
    assert "my-cluster" in yaml_str
    assert "best-effort-large" in yaml_str
    assert "Cluster" in yaml_str


def test_generate_tkc_yaml_invalid_worker_count():
    with pytest.raises(ValueError, match="worker_count"):
        generate_tkc_yaml(
            name="bad", namespace="dev", k8s_version="v1.28.4+vmware.1",
            vm_class="best-effort-large", control_plane_count=1,
            worker_count=0, storage_class="vsphere-storage",
        )


def test_generate_tkc_yaml_invalid_control_plane():
    with pytest.raises(ValueError, match="control_plane_count"):
        generate_tkc_yaml(
            name="bad", namespace="dev", k8s_version="v1.28.4+vmware.1",
            vm_class="best-effort-large", control_plane_count=2,
            worker_count=3, storage_class="vsphere-storage",
        )


def test_list_tkc_clusters_empty():
    si = MagicMock()
    mock_api = MagicMock()
    mock_api.list_namespaced_custom_object.return_value = {"items": []}
    with (
        patch("vmware_vks.ops.tkc._get_custom_objects_api", return_value=mock_api),
        # _resolve_tkc_version was added after this test; it hits Supervisor
        # discovery (_rest_get) and must be stubbed for the unit test.
        patch("vmware_vks.ops.tkc._resolve_tkc_version", return_value="v1alpha3"),
    ):
        result = list_tkc_clusters(si, namespace="dev")
    assert result["items"] == []
    assert result["total"] == 0


def test_get_tkc_cluster_reports_all_worker_pools_and_total():
    api = MagicMock()
    api.get_namespaced_custom_object.return_value = {
        "spec": {"topology": {"workers": {"machineDeployments": [
            {"name": "pool-a", "replicas": 2},
            {"name": "pool-b", "replicas": 4},
        ]}}},
        "status": {"phase": "Running"},
    }
    with (
        patch("vmware_vks.ops.tkc._get_custom_objects_api", return_value=api),
        patch("vmware_vks.ops.tkc._resolve_tkc_version", return_value="v1beta1"),
    ):
        result = get_tkc_cluster(MagicMock(), "c1", "dev")
    assert result["worker_replicas"] == 2  # legacy first-pool field
    assert result["worker_pools"] == [
        {"name": "pool-a", "replicas": 2},
        {"name": "pool-b", "replicas": 4},
    ]
    assert result["worker_replica_total"] == 6


def test_get_tkc_cluster_autoscaled_pool_keeps_total_unknown():
    api = MagicMock()
    api.get_namespaced_custom_object.return_value = {
        "spec": {"topology": {"workers": {"machineDeployments": [
            {"name": "fixed", "replicas": 2},
            {"name": "auto", "metadata": {"annotations": {
                "cluster.x-k8s.io/cluster-api-autoscaler-node-group-min-size": "1",
                "cluster.x-k8s.io/cluster-api-autoscaler-node-group-max-size": "5",
            }}},
        ]}}},
        "status": {},
    }
    with (
        patch("vmware_vks.ops.tkc._get_custom_objects_api", return_value=api),
        patch("vmware_vks.ops.tkc._resolve_tkc_version", return_value="v1beta1"),
    ):
        result = get_tkc_cluster(MagicMock(), "c1", "dev")
    assert result["worker_replica_total"] is None
    assert result["worker_pools"][1] == {
        "name": "auto", "replicas": None, "autoscaled": True,
        "min_size": 1, "max_size": 5,
    }


def test_delete_tkc_cluster_requires_confirmed():
    # dry_run=False + confirmed=False must be refused (workload guard clean).
    si = MagicMock()
    with patch("vmware_vks.ops.tkc._check_running_workloads", return_value=[]):
        with pytest.raises(ValueError, match="confirmed=True"):
            delete_tkc_cluster(si, "my-cluster", "dev", confirmed=False, dry_run=False)


def test_delete_tkc_cluster_dry_run():
    si = MagicMock()
    with patch("vmware_vks.ops.tkc._check_running_workloads", return_value=[]):
        result = delete_tkc_cluster(si, "my-cluster", "dev", confirmed=True, dry_run=True)
    assert result["dry_run"] is True


def test_delete_tkc_cluster_rejects_with_workloads():
    si = MagicMock()
    workloads = [{"kind": "Deployment", "name": "app", "namespace": "default"}]
    with patch("vmware_vks.ops.tkc._check_running_workloads", return_value=workloads):
        with pytest.raises(RuntimeError, match="Deployment/app"):
            delete_tkc_cluster(si, "my-cluster", "dev", confirmed=True, dry_run=False, force=False)


def test_tkc_op_resolves_supervisor_endpoint_once(monkeypatch):
    """A single TKC op (version probe + CustomObjectsApi build = 2 client builds)
    must hit the vCenter cluster-discovery REST round-trips only ONCE, via the
    per-host endpoint cache (issue #12)."""
    import vmware_vks.k8s_connection as k8sc
    from vmware_vks.ops import tkc

    # Isolate per-host caches so prior tests / ordering can't poison this one.
    k8sc._endpoint_cache.clear()
    tkc._version_cache.clear()

    rest_calls: list[str] = []

    def fake_rest_get(si, path):
        rest_calls.append(path)
        if path.endswith("/clusters"):
            return [{"cluster": "domain-c1", "config_status": "RUNNING"}]
        return {"api_server_cluster_endpoint": "10.0.0.1:6443"}

    monkeypatch.setattr("vmware_vks.ops.supervisor._rest_get", fake_rest_get)
    monkeypatch.setattr("vmware_vks.wcp_login.get_wcp_token", lambda si: "jwt-token")
    monkeypatch.setattr("vmware_vks.connection.get_verify_ssl", lambda si: False)

    # Count kubernetes client builds and stub discovery to land on v1beta1.
    build_count = {"n": 0}
    real_load = MagicMock()

    fake_k8s = MagicMock()
    fake_k8s.config.load_kube_config_from_dict = real_load

    def fake_api_client(*a, **k):
        build_count["n"] += 1
        return MagicMock()

    fake_k8s.client.ApiClient.side_effect = fake_api_client
    fake_group = MagicMock()
    fake_group.name = "cluster.x-k8s.io"
    fake_ver = MagicMock()
    fake_ver.version = "v1beta1"
    fake_group.versions = [fake_ver]
    fake_k8s.client.ApisApi.return_value.get_api_versions.return_value.groups = [fake_group]

    si = MagicMock()
    si._stub.host = "vc.example.com"
    mock_api = fake_k8s.client.CustomObjectsApi.return_value
    mock_api.list_namespaced_custom_object.return_value = {"items": []}

    with patch.dict("sys.modules", {"kubernetes": fake_k8s}):
        result = tkc.list_tkc_clusters(si, namespace="dev")

    assert result["total"] == 0
    # Two client builds (version probe + CustomObjectsApi)...
    assert build_count["n"] == 2
    # ...but the two cluster-discovery REST calls ran only once total.
    assert rest_calls == [
        "/vcenter/namespace-management/clusters",
        "/vcenter/namespace-management/clusters/domain-c1",
    ]
