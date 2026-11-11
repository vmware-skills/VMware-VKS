"""Regression evals for vSphere Automation REST API shape bugs (2026-06).

Findings verified against the official vSphere Automation SDK structure
definitions and the Broadcom API reference:

C1. namespace-management/storage/storage-policies does not exist (404) —
    the real list is GET /api/vcenter/storage/policies with items
    {policy, name, description}.
H1. VirtualMachineClasses.Info: memory wire name is memory_MB; GPU info
    nests under devices (vgpu_devices / dynamic_direct_path_io_devices) —
    there is no flat gpu_count.
H2. GET /api/vcenter/content/registries/harbor returns Summary[] =
    {cluster, registry, version, ui_access_url}; storage/health live on
    the per-registry Harbor.Info detail.
H3. Clusters.Info has no current_kubernetes_version — version comes from
    GET /api/vcenter/namespace-management/software/clusters/{cluster}
    → current_version.

All REST traffic is mocked at the _rest_get helper.
"""
from unittest.mock import MagicMock, patch

from vmware_vks.ops.harbor import get_harbor_info
from vmware_vks.ops.namespace import list_vm_classes
from vmware_vks.ops.supervisor import (
    get_supervisor_status,
    list_supervisor_storage_policies,
)


def _mock_si():
    si = MagicMock()
    si.content.about.version = "8.0.2"
    si.content.sessionManager.currentSession.key = "session-123"
    si._stub.host = "vcenter.example.com:443"
    return si


# ---------------------------------------------------------------------------
# C1 — storage policies must use GET /api/vcenter/storage/policies
# ---------------------------------------------------------------------------

def test_storage_policies_calls_general_vcenter_endpoint():
    si = _mock_si()
    with patch("vmware_vks.ops.supervisor._rest_get") as mock_get:
        mock_get.return_value = []
        list_supervisor_storage_policies(si)
    mock_get.assert_called_once_with(si, "/vcenter/storage/policies")


def test_storage_policies_parses_policy_name_description():
    si = _mock_si()
    with patch("vmware_vks.ops.supervisor._rest_get") as mock_get:
        mock_get.return_value = [
            {
                "policy": "aa6d5a82-1c88-45da-85d3-3d74b91a5bad",
                "name": "vSAN Default Storage Policy",
                "description": "Storage policy used as default for vSAN datastores",
            }
        ]
        result = list_supervisor_storage_policies(si)
    assert result["items"] == [
        {
            "policy": "aa6d5a82-1c88-45da-85d3-3d74b91a5bad",
            "name": "vSAN Default Storage Policy",
            "description": "Storage policy used as default for vSAN datastores",
        }
    ]


def test_storage_policies_tolerates_missing_description():
    si = _mock_si()
    with patch("vmware_vks.ops.supervisor._rest_get") as mock_get:
        mock_get.return_value = [{"policy": "p-1", "name": "Gold"}]
        result = list_supervisor_storage_policies(si)
    assert result["items"][0]["policy"] == "p-1"
    assert result["items"][0]["name"] == "Gold"
    assert result["items"][0]["description"] == ""


# ---------------------------------------------------------------------------
# H1 — VM classes: memory_MB wire name + GPU devices nesting
# ---------------------------------------------------------------------------

def test_vm_classes_parses_memory_mb_wire_name():
    si = _mock_si()
    with patch("vmware_vks.ops.namespace._rest_get") as mock_get:
        mock_get.return_value = [
            {"id": "best-effort-large", "cpu_count": 4, "memory_MB": 8192}
        ]
        result = list_vm_classes(si)
    assert result["items"][0]["memory_mb"] == 8192


def test_vm_classes_derives_gpu_count_from_devices():
    si = _mock_si()
    with patch("vmware_vks.ops.namespace._rest_get") as mock_get:
        mock_get.return_value = [
            {
                "id": "gpu-class",
                "cpu_count": 8,
                "memory_MB": 32768,
                "devices": {
                    "vgpu_devices": [{"profile_name": "grid_v100-4q"}],
                    "dynamic_direct_path_io_devices": [
                        {"vendor_id": 4318, "device_id": 7864}
                    ],
                },
            },
            {"id": "no-gpu-class", "cpu_count": 2, "memory_MB": 4096},
        ]
        result = list_vm_classes(si)
    assert result["items"][0]["gpu_count"] == 2
    assert result["items"][1]["gpu_count"] == 0


# ---------------------------------------------------------------------------
# H2 — Harbor: Summary[] shape + per-registry Info enrichment
# ---------------------------------------------------------------------------

_HARBOR_SUMMARY = [
    {
        "cluster": "domain-c8",
        "registry": "registry-1",
        "version": "v2.7.1",
        "ui_access_url": "https://harbor.example.com",
    }
]


def test_harbor_parses_summary_wire_fields():
    si = _mock_si()

    def fake_get(si_arg, path):
        if path == "/vcenter/content/registries/harbor":
            return _HARBOR_SUMMARY
        if path == "/vcenter/content/registries/harbor/registry-1":
            return {
                "health": {"status": "RUNNING"},
                "storage": [{"policy": "p-1", "capacity": 4096, "used": 1024}],
            }
        raise AssertionError(f"unexpected path {path}")

    with patch("vmware_vks.ops.harbor._rest_get", side_effect=fake_get):
        result = get_harbor_info(si)

    reg = result["registries"][0]
    assert reg["id"] == "registry-1"
    assert reg["cluster"] == "domain-c8"
    assert reg["version"] == "v2.7.1"
    assert reg["url"] == "https://harbor.example.com"
    assert reg["status"] == "RUNNING"
    assert reg["storage_used_mb"] == 1024


def test_harbor_enrichment_degrades_gracefully():
    si = _mock_si()

    def fake_get(si_arg, path):
        if path == "/vcenter/content/registries/harbor":
            return _HARBOR_SUMMARY
        raise RuntimeError("REST GET failed (500)")

    with patch("vmware_vks.ops.harbor._rest_get", side_effect=fake_get):
        result = get_harbor_info(si)

    reg = result["registries"][0]
    assert reg["id"] == "registry-1"
    assert reg["url"] == "https://harbor.example.com"
    assert reg["status"] is None
    assert reg["storage_used_mb"] is None


# ---------------------------------------------------------------------------
# H3 — Supervisor status: k8s version from software/clusters endpoint
# ---------------------------------------------------------------------------

_CLUSTER_INFO = {
    "config_status": "RUNNING",
    "kubernetes_status": "READY",
    "api_server_cluster_endpoint": "10.0.0.1:6443",
    "network_provider": "NSXT_CONTAINER_PLUGIN",
}


def test_supervisor_status_reads_version_from_software_endpoint():
    si = _mock_si()

    def fake_get(si_arg, path):
        if path == "/vcenter/namespace-management/clusters/domain-c8":
            return _CLUSTER_INFO
        if path == "/vcenter/namespace-management/software/clusters/domain-c8":
            return {"current_version": "v1.27.5+vmware.1-fp.1"}
        raise AssertionError(f"unexpected path {path}")

    with patch("vmware_vks.ops.supervisor._rest_get", side_effect=fake_get):
        result = get_supervisor_status(si, "domain-c8")

    assert result["kubernetes_version"] == "v1.27.5+vmware.1-fp.1"
    # The four real Clusters.Info fields must still be parsed.
    assert result["config_status"] == "RUNNING"
    assert result["kubernetes_status"] == "READY"
    assert result["api_server_cluster_endpoint"] == "10.0.0.1:6443"
    assert result["network_provider"] == "NSXT_CONTAINER_PLUGIN"


def test_supervisor_status_version_degrades_with_hint():
    si = _mock_si()

    def fake_get(si_arg, path):
        if path == "/vcenter/namespace-management/clusters/domain-c8":
            return _CLUSTER_INFO
        from vmware_vks.errors import VksApiError
        raise VksApiError("REST GET failed", status_code=404)

    with patch("vmware_vks.ops.supervisor._rest_get", side_effect=fake_get):
        result = get_supervisor_status(si, "domain-c8")

    assert result["kubernetes_version"] is None
    assert "404" in result["kubernetes_version_hint"]
    assert result["config_status"] == "RUNNING"
