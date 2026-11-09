"""Unknown API capabilities must never become cached permission to read/write."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import kubernetes as k8s
import pytest

from vmware_vks.errors import VksApiError
from vmware_vks.ops import tkc


def discovered(*versions):
    return SimpleNamespace(
        groups=[
            SimpleNamespace(
                name="cluster.x-k8s.io",
                versions=[SimpleNamespace(version=v) for v in versions],
            )
        ]
    )


@pytest.fixture
def discovery(monkeypatch):
    probe = MagicMock()
    probe.get_api_versions.return_value = discovered("v1beta1")
    clients = []

    def client(si, namespace):
        api = MagicMock()
        clients.append(api)
        return api

    monkeypatch.setattr("vmware_vks.k8s_connection.get_k8s_client", client)
    monkeypatch.setattr(k8s.client, "ApisApi", lambda api: probe)
    resources = MagicMock()
    resources.return_value.list_namespaced_custom_object.return_value = {"items": []}
    monkeypatch.setattr(tkc, "_get_custom_objects_api", resources)
    # Isolate the pre-fix cache too, so each red control tests its own input.
    getattr(tkc, "_version_cache", {}).clear()
    si = SimpleNamespace(_stub=SimpleNamespace(host="vc.example.invalid"))
    return si, probe, clients, resources


@pytest.mark.parametrize(
    "versions", [("v1", "v1beta1"), ("v1beta2", "v1beta1"), ("v1beta1",)]
)
def test_select_only_the_schema_this_implementation_supports(discovery, versions):
    si, probe, clients, _ = discovery
    probe.get_api_versions.return_value = discovered(*versions)
    assert tkc._resolve_tkc_version(si, "dev") == "v1beta1"
    clients[0].close.assert_called_once()


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(groups=[]),
        SimpleNamespace(groups=None),
        discovered("v1"),
        SimpleNamespace(groups=[SimpleNamespace(name="cluster.x-k8s.io")]),
        SimpleNamespace(
            groups=[SimpleNamespace(name="cluster.x-k8s.io", versions="v1beta1")]
        ),
    ],
)
def test_absent_unsupported_or_malformed_group_stops_resource_calls(
    discovery, response
):
    si, probe, clients, resources = discovery
    probe.get_api_versions.return_value = response
    with pytest.raises(VksApiError):
        tkc.list_tkc_clusters(si, "dev")
    resources.assert_not_called()
    clients[0].close.assert_called_once()


@pytest.mark.parametrize("status", [401, 403, 500])
def test_auth_or_server_failure_does_not_become_a_fallback(
    discovery, monkeypatch, caplog, status
):
    si, probe, clients, resources = discovery
    invalidated = []
    monkeypatch.setattr(
        "vmware_vks.wcp_login.invalidate_wcp_token_for_si", invalidated.append
    )
    error = k8s.client.exceptions.ApiException(
        status=status, reason="opaque-private-token"
    )
    error.body = "opaque-private-token"
    probe.get_api_versions.side_effect = error
    with pytest.raises(VksApiError) as caught:
        tkc.create_tkc_cluster(si, "app", "dev", "v1.28.4", "vm-class", dry_run=False)
    assert caught.value.status_code == status
    assert "opaque-private-token" not in str(caught.value) + caplog.text
    assert bool(invalidated) == (status == 401)
    resources.assert_not_called()
    clients[0].close.assert_called_once()


def test_failed_discovery_can_recover_without_restarting(discovery, caplog):
    si, probe, clients, resources = discovery
    probe.get_api_versions.side_effect = [
        ConnectionError("opaque-private-token"),
        discovered("v1beta1"),
    ]
    with pytest.raises(VksApiError):
        tkc._resolve_tkc_version(si, "dev")
    assert tkc._resolve_tkc_version(si, "dev") == "v1beta1"
    assert probe.get_api_versions.call_count == 2
    assert "opaque-private-token" not in caplog.text
    assert all(c.close.call_count == 1 for c in clients)
    resources.assert_not_called()


def test_previous_session_or_namespace_does_not_supply_capability(discovery):
    si, probe, clients, _ = discovery
    assert tkc._resolve_tkc_version(si, "dev") == "v1beta1"
    probe.get_api_versions.return_value = discovered("v1")
    another = SimpleNamespace(_stub=SimpleNamespace(host=si._stub.host))
    with pytest.raises(VksApiError):
        tkc._resolve_tkc_version(another, "prod")
    with pytest.raises(VksApiError):
        tkc._resolve_tkc_version(si, "dev")
    assert probe.get_api_versions.call_count == 3
    assert len(clients) == 3 and all(c.close.call_count == 1 for c in clients)


@pytest.mark.parametrize("version", ["v1", "v1alpha3"])
def test_offline_generator_rejects_unimplemented_schemas(version):
    with pytest.raises(ValueError, match="api_version"):
        tkc.generate_tkc_yaml(
            "app", "dev", "v1.28.4", "vm-class", 1, 3, "storage", api_version=version
        )


def test_offline_preview_is_explicitly_unvalidated():
    result = tkc.create_tkc_cluster(
        None, "app", "dev", "v1.28.4", "vm-class", dry_run=True
    )
    assert result["validated"] is False
    assert "not validated" in result["hint"].lower()


@pytest.mark.parametrize("confirm", [False, True])
def test_mcp_create_cannot_preview_or_confirm_an_unsupported_schema(
    discovery, monkeypatch, confirm
):
    from vmware_policy.audit import get_engine, reset_engine
    from vmware_vks.mcp_server import server

    si, probe, _, resources = discovery
    probe.get_api_versions.return_value = discovered("v1")
    monkeypatch.setattr(server, "_get_si", lambda target=None: si)
    reset_engine()
    result = server.create_tkc_cluster(
        name="app", namespace="dev", k8s_version="v1.28.4", vm_class="vm-class",
        target="lab-vc", confirm=confirm,
    )
    assert "error" in result and "v1beta1" in result["error"]
    resources.assert_not_called()
    row = get_engine().query(skill="vks", tool="create_tkc_cluster", limit=1)[0]
    assert row["status"] == "error"
