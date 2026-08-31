"""Tests for kubeconfig ops."""
from unittest.mock import MagicMock, patch
import pytest
from vmware_vks.ops.kubeconfig import (
    get_supervisor_kubeconfig_str,
    get_tkc_kubeconfig_str,
    write_kubeconfig,
)


def _mock_si():
    si = MagicMock()
    si.content.about.version = "8.0.2"
    si._stub.host = "vcenter.example.com:443"
    si.content.sessionManager.currentSession.key = "session-123"
    return si


def test_get_supervisor_kubeconfig_returns_string():
    si = _mock_si()
    with patch("vmware_vks.ops.kubeconfig.get_supervisor_kubeconfig_str") as mock_get:
        # Since the module-level function delegates to k8s_connection,
        # we patch at the import target to verify the delegation.
        pass
    # Patch the inner import instead
    with patch("vmware_vks.k8s_connection.get_supervisor_kubeconfig_str") as mock_inner:
        mock_inner.return_value = "apiVersion: v1\nkind: Config\nclusters: []\n"
        result = get_supervisor_kubeconfig_str(si, "dev")
    assert isinstance(result, str)
    assert "apiVersion" in result


def test_get_tkc_kubeconfig_returns_string():
    si = _mock_si()
    mock_api_client = MagicMock()
    mock_custom_api = MagicMock()
    mock_custom_api.get_namespaced_custom_object.return_value = {
        "spec": {
            "controlPlaneEndpoint": {
                "host": "10.0.0.100",
                "port": 6443,
            }
        }
    }

    with patch("vmware_vks.k8s_connection.get_k8s_client", return_value=mock_api_client):
        with patch("kubernetes.client.CustomObjectsApi", return_value=mock_custom_api):
            with patch(
                "vmware_vks.wcp_login.get_wcp_token", return_value="wcp-jwt-token"
            ):
                result = get_tkc_kubeconfig_str(si, "my-cluster", "dev")

    assert isinstance(result, str)
    assert "my-cluster" in result
    assert "https://10.0.0.100:6443" in result
    # Bearer token must be the /wcp/login JWT — NOT the pyVmomi SOAP session key.
    assert "wcp-jwt-token" in result
    assert "session-123" not in result


def test_write_kubeconfig_to_file(tmp_path):
    si = _mock_si()
    output_file = tmp_path / "kubeconfig.yaml"
    fake_kubeconfig = "apiVersion: v1\nkind: Config\nclusters: []\n"

    with patch("vmware_vks.ops.kubeconfig.get_tkc_kubeconfig_str", return_value=fake_kubeconfig):
        result = write_kubeconfig(si, "my-cluster", "dev", output_path=output_file)

    assert output_file.exists()
    assert output_file.read_text(encoding="utf-8") == fake_kubeconfig
    # Verify file permissions are 0o600
    assert oct(output_file.stat().st_mode & 0o777) == oct(0o600)
    assert result["cluster"] == "my-cluster"
    assert result["written_to"] == str(output_file)
