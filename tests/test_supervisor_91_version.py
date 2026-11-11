from types import SimpleNamespace
import pytest
from vmware_vks.ops import supervisor


def run(monkeypatch, *, version="9.1.0", fail_version=False, both_match=False, invalid_list=False, invalid_version=False):
    calls = []
    def read(si, path):
        calls.append(path)
        if path == "/vcenter/namespace-management/clusters/domain-c1":
            return {"config_status":"RUNNING", "kubernetes_status":"READY"}
        if path.endswith("/supervisors/summaries"):
            return {"items": None} if invalid_list else {"items":[{"supervisor":"sup-other"},{"supervisor":"sup-right"}]}
        if path.endswith("/sup-other/topology"):
            return [{"zone":"zone-a", "clusters":["domain-c1" if both_match else "domain-c2"]}]
        if path.endswith("/sup-right/topology"):
            return [{"zone":"zone-b", "clusters":["domain-c1"]}]
        if path.endswith("/software/supervisors/upgrades/sup-right"):
            if fail_version:
                from vmware_vks.errors import VksApiError
                raise VksApiError("fixture-secret token rejected", status_code=403)
            return {} if invalid_version else {"current_version":"v1.34.9+vmware.1"}
        if "/software/clusters/" in path:
            return {"current_version":"obsolete-wrong-version"}
        raise AssertionError(path)
    monkeypatch.setattr(supervisor,"_rest_get",read)
    si=SimpleNamespace(content=SimpleNamespace(about=SimpleNamespace(version=version)))
    return supervisor.get_supervisor_status(si,"domain-c1"),calls


def test_91_uses_topology_identity_instead_of_first_supervisor_or_cluster_moref(monkeypatch):
    result,calls=run(monkeypatch)
    assert result["kubernetes_version"]=="v1.34.9+vmware.1"
    assert not any("/software/clusters/" in c for c in calls)
    assert calls[-1].endswith("/sup-right")


@pytest.mark.parametrize("scenario", ["fail_version", "both_match", "invalid_list", "invalid_version"])
def test_91_missing_or_denied_evidence_stays_unknown_without_privilege_fallback(monkeypatch,scenario):
    result,calls=run(monkeypatch,**{scenario:True})
    assert result["config_status"]=="RUNNING"
    assert result["kubernetes_version"] is None and result.get("kubernetes_version_hint")
    assert "fixture-secret" not in str(result)
    if scenario == "fail_version":assert "403" in result["kubernetes_version_hint"]
    assert not any("/software/clusters/" in c for c in calls)


def test_80_keeps_working_legacy_path_without_supervisor_discovery(monkeypatch):
    result,calls=run(monkeypatch,version="8.0.3")
    assert result["kubernetes_version"]=="obsolete-wrong-version"
    assert len(calls)==2
