"""Regression evals for the 2026-06 fix pack.

One test (or group) per confirmed bug:

#2  scale_tkc_cluster merge-patch wiped other node pools and the `class` field
#3  namespace-delete TKC guard failed OPEN on API errors
#4  centralized error translation (REST + K8s + CLI + MCP) — 踩坑 #37
#5  dry_run must be evaluated before confirmed
#6  CLI writes were unaudited
#7  unguarded nested topology lookups crashed on half-provisioned clusters
#8  tkc delete --force skipped the workload GUARD (renamed --skip-workload-check)
#9  SKILL.md tool-count header vs actual mcp.list_tools() parity
#10 dead duplicate _vcenter_host in k8s_connection
"""
from __future__ import annotations

import io
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from vmware_vks.errors import VksApiError


def _mock_si():
    si = MagicMock()
    si._stub.host = "vcenter.example.com:443"
    si.content.sessionManager.currentSession.key = "session-123"
    return si


def _mock_si_with_rest_session():
    """A connection whose REST session id is already cached.

    These tests are about how an HTTP status is translated, not about how the
    session is obtained. Since ``_rest_request`` now fetches a session id from
    ``POST /api/session`` (the SOAP key it used to send is not a REST token —
    see ``test_rest_session_token.py``), a bare mock would spend the patched
    ``urlopen`` on the login call and never reach the status under test. Seeding
    the cache keeps each test pointed at its own subject.
    """
    from vmware_vks.config import TargetConfig
    from vmware_vks.connection import _SI_TARGET, _SI_VERIFY_SSL
    from vmware_vks.rest_session import _token_cache

    si = _mock_si()
    target = TargetConfig(
        name="fixpack",
        host="vcenter.example.com",
        config_username="svc@vsphere.local",
    )
    _SI_TARGET[id(si)] = target
    _SI_VERIFY_SSL[id(si)] = True
    _token_cache[("vcenter.example.com", "svc@vsphere.local")] = "rest-session-id"
    return si


# ---------------------------------------------------------------------------
# #2 — scale preserves all node pools and their class fields
# ---------------------------------------------------------------------------

_TWO_POOL_CLUSTER = {
    "spec": {
        "topology": {
            "workers": {
                "machineDeployments": [
                    {"class": "node-pool", "name": "pool-a", "replicas": 3,
                     "metadata": {}},
                    {"class": "node-pool", "name": "pool-b", "replicas": 2,
                     "metadata": {}},
                ]
            }
        }
    }
}


def _scale(si, pool_name=None, worker_count=5):
    from vmware_vks.ops.tkc import scale_tkc_cluster
    api = MagicMock()
    api.get_namespaced_custom_object.return_value = _TWO_POOL_CLUSTER
    with (
        patch("vmware_vks.ops.tkc._get_custom_objects_api", return_value=api),
        patch("vmware_vks.ops.tkc._resolve_tkc_version", return_value="v1beta1"),
    ):
        result = scale_tkc_cluster(
            si, "c1", "dev", worker_count, pool_name=pool_name
        )
    return api, result


def test_scale_patch_preserves_class_and_other_pools():
    api, result = _scale(_mock_si(), pool_name="pool-a")
    body = api.patch_namespaced_custom_object.call_args.kwargs["body"]
    pools = body["spec"]["topology"]["workers"]["machineDeployments"]
    assert len(pools) == 2, "other pools must NOT be wiped by the merge patch"
    assert pools[0] == {"class": "node-pool", "name": "pool-a", "replicas": 5,
                        "metadata": {}}
    assert pools[1] == {"class": "node-pool", "name": "pool-b", "replicas": 2,
                        "metadata": {}}
    assert result["pool"] == "pool-a"


def test_scale_default_targets_first_existing_pool_not_hardcoded_name():
    api, result = _scale(_mock_si(), pool_name=None)
    body = api.patch_namespaced_custom_object.call_args.kwargs["body"]
    pools = body["spec"]["topology"]["workers"]["machineDeployments"]
    assert pools[0]["replicas"] == 5
    assert pools[1]["replicas"] == 2
    assert result["pool"] == "pool-a"  # first existing, NOT "worker-pool"


def test_scale_unknown_pool_raises_teaching_error():
    with pytest.raises(VksApiError, match="pool-a, pool-b"):
        _scale(_mock_si(), pool_name="nope")


def test_scale_does_not_mutate_fetched_cluster():
    original_replicas = _TWO_POOL_CLUSTER["spec"]["topology"]["workers"][
        "machineDeployments"][0]["replicas"]
    _scale(_mock_si(), pool_name="pool-a")
    after = _TWO_POOL_CLUSTER["spec"]["topology"]["workers"][
        "machineDeployments"][0]["replicas"]
    assert after == original_replicas


def test_scale_no_pools_raises_teaching_error():
    from vmware_vks.ops.tkc import scale_tkc_cluster
    api = MagicMock()
    api.get_namespaced_custom_object.return_value = {"spec": {"topology": {}}}
    with (
        patch("vmware_vks.ops.tkc._get_custom_objects_api", return_value=api),
        patch("vmware_vks.ops.tkc._resolve_tkc_version", return_value="v1beta1"),
    ):
        with pytest.raises(VksApiError, match="no machineDeployments"):
            scale_tkc_cluster(_mock_si(), "c1", "dev", 5)


# ---------------------------------------------------------------------------
# #3 — namespace delete TKC guard fails CLOSED on API errors
# ---------------------------------------------------------------------------

def test_namespace_delete_refused_when_tkc_check_fails():
    from vmware_vks.ops.namespace import delete_namespace
    si = _mock_si()
    with patch(
        "vmware_vks.ops.tkc.list_tkc_clusters",
        side_effect=RuntimeError("Supervisor unreachable"),
    ):
        with pytest.raises(RuntimeError, match="Cannot verify TKC clusters in 'dev'"):
            delete_namespace(si, "dev", confirmed=True, dry_run=False)


# ---------------------------------------------------------------------------
# #4a — REST layer translates HTTP errors into teaching VksApiError
# ---------------------------------------------------------------------------

def _http_error(code: int, msg: str = "err") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://vc/api/x", code, msg, None, io.BytesIO(b"detail")
    )


def test_rest_404_raises_teaching_error():
    from vmware_vks.ops.supervisor import _rest_get
    with patch("urllib.request.urlopen", side_effect=_http_error(404)):
        with pytest.raises(VksApiError) as exc_info:
            _rest_get(_mock_si_with_rest_session(), "/vcenter/namespaces/instances/missing")
    assert exc_info.value.status_code == 404
    assert "vmware-vks namespace list" in str(exc_info.value)


def test_rest_403_raises_permission_hint():
    from vmware_vks.ops.supervisor import _rest_get
    with patch("urllib.request.urlopen", side_effect=_http_error(403)):
        with pytest.raises(VksApiError, match="Workload Management"):
            _rest_get(_mock_si_with_rest_session(), "/vcenter/namespaces/instances")


def test_rest_503_get_retries_exactly_once():
    from vmware_vks.ops.supervisor import _rest_get
    with (
        patch("urllib.request.urlopen", side_effect=_http_error(503)) as mock_open,
        patch("vmware_vks.ops.supervisor.time.sleep"),
    ):
        with pytest.raises(VksApiError, match="not ready"):
            _rest_get(_mock_si_with_rest_session(), "/vcenter/namespaces/instances")
    assert mock_open.call_count == 2  # initial + exactly one retry


def test_rest_503_post_is_not_retried():
    from vmware_vks.ops.supervisor import _rest_post
    with (
        patch("urllib.request.urlopen", side_effect=_http_error(503)) as mock_open,
        patch("vmware_vks.ops.supervisor.time.sleep"),
    ):
        with pytest.raises(VksApiError):
            _rest_post(_mock_si_with_rest_session(), "/vcenter/namespaces/instances", {"x": 1})
    assert mock_open.call_count == 1  # writes are never blind-retried


def test_rest_urlerror_translated_not_raw():
    from vmware_vks.ops.supervisor import _rest_post
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        with pytest.raises(VksApiError, match="vmware-vks check"):
            _rest_post(_mock_si_with_rest_session(), "/vcenter/namespaces/instances", {"x": 1})


# ---------------------------------------------------------------------------
# #4b — kubernetes ApiException translated into teaching VksApiError
# ---------------------------------------------------------------------------

def test_k8s_404_translated_with_list_hint():
    from kubernetes.client.exceptions import ApiException
    from vmware_vks.ops.tkc import get_tkc_cluster

    api = MagicMock()
    api.get_namespaced_custom_object.side_effect = ApiException(
        status=404, reason="Not Found"
    )
    with (
        patch("vmware_vks.ops.tkc._get_custom_objects_api", return_value=api),
        patch("vmware_vks.ops.tkc._resolve_tkc_version", return_value="v1beta1"),
    ):
        with pytest.raises(VksApiError) as exc_info:
            get_tkc_cluster(_mock_si(), "ghost", "dev")
    msg = str(exc_info.value)
    assert "TKC 'ghost' not found" in msg
    assert "list_tkc_clusters" in msg
    assert exc_info.value.status_code == 404


def test_k8s_401_translated_and_invalidates_wcp_token():
    from kubernetes.client.exceptions import ApiException
    from vmware_vks.k8s_connection import translate_k8s_error

    si = _mock_si()
    with patch("vmware_vks.wcp_login.invalidate_wcp_token_for_si") as mock_inv:
        err = translate_k8s_error(
            si, ApiException(status=401, reason="Unauthorized"),
            resource="c1", namespace="dev",
        )
    assert isinstance(err, VksApiError)
    assert "SSO credentials" in str(err)
    mock_inv.assert_called_once_with(si)


# ---------------------------------------------------------------------------
# #4c — CLI decorator: teaching one-liner + exit 1, no traceback
# ---------------------------------------------------------------------------

def test_cli_translates_vks_api_error_to_red_line_and_exit_1():
    from typer.testing import CliRunner
    from vmware_vks.cli import app

    runner = CliRunner()
    with patch(
        "vmware_vks.cli._get_si",
        side_effect=VksApiError("Supervisor not ready — wait and retry"),
    ):
        result = runner.invoke(app, ["namespace", "list"])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "Supervisor not ready" in result.output
    assert "Traceback" not in result.output


def test_cli_translates_runtime_error_to_exit_1():
    from typer.testing import CliRunner
    from vmware_vks.cli import app

    runner = CliRunner()
    with patch(
        "vmware_vks.cli._get_si", side_effect=FileNotFoundError("config missing")
    ):
        result = runner.invoke(app, ["tkc", "list"])
    assert result.exit_code == 1
    assert "Error:" in result.output


# ---------------------------------------------------------------------------
# #4d — MCP write tools return {"error": ...} instead of raw re-raise
# ---------------------------------------------------------------------------

def test_mcp_scale_returns_error_dict_and_audits():
    import vmware_vks.mcp_server.server as srv

    boom = VksApiError("TKC 'c1' not found in namespace 'dev' — run "
                       "list_tkc_clusters to see available clusters.")
    with (
        patch.object(srv, "_get_si", side_effect=boom),
        patch.object(srv._audit, "log") as mock_log,
    ):
        result = srv.scale_tkc_cluster("c1", "dev", 5)
    assert isinstance(result, dict)
    assert "not found" in result["error"]
    audit_kwargs = mock_log.call_args.kwargs
    assert audit_kwargs["result"].startswith("error:")


def test_mcp_delete_tkc_returns_error_dict():
    import vmware_vks.mcp_server.server as srv

    with patch.object(srv, "_get_si", side_effect=VksApiError("boom", 503)):
        result = srv.delete_tkc_cluster("c1", "dev", confirmed=True, dry_run=False)
    assert "error" in result


def test_safe_error_passes_vks_api_error_through():
    import vmware_vks.mcp_server.server as srv

    msg = srv._safe_error(VksApiError("teaching hint here"), "tool")
    assert "teaching hint here" in msg
    # Non-allowlisted exceptions stay opaque
    assert "secret" not in srv._safe_error(Exception("secret detail"), "tool")


# ---------------------------------------------------------------------------
# #5 — dry_run evaluated BEFORE confirmed (preview never needs confirmation)
# ---------------------------------------------------------------------------

def test_namespace_delete_dry_run_does_not_require_confirmed():
    from vmware_vks.ops.namespace import delete_namespace
    with patch("vmware_vks.ops.namespace._list_tkc_in_namespace", return_value=[]):
        result = delete_namespace(_mock_si(), "dev", confirmed=False, dry_run=True)
    assert result["dry_run"] is True


def test_tkc_delete_dry_run_does_not_require_confirmed():
    from vmware_vks.ops.tkc import delete_tkc_cluster
    with patch("vmware_vks.ops.tkc._check_running_workloads", return_value=[]):
        result = delete_tkc_cluster(
            _mock_si(), "c1", "dev", confirmed=False, dry_run=True
        )
    assert result["dry_run"] is True


# ---------------------------------------------------------------------------
# #6 — CLI writes are audited; audit failure degrades to stderr warning
# ---------------------------------------------------------------------------

def test_cli_tkc_scale_writes_audit_entry():
    from typer.testing import CliRunner
    from vmware_vks.cli import app

    runner = CliRunner()
    with (
        patch("vmware_vks.cli._get_si", return_value=_mock_si()),
        patch(
            "vmware_vks.ops.tkc.scale_tkc_cluster",
            return_value={"name": "c1", "status": "scaling"},
        ),
        patch("vmware_vks.notify.audit.AuditLogger.log") as mock_log,
    ):
        result = runner.invoke(app, ["tkc", "scale", "c1", "-n", "dev", "--workers", "5"])
    assert result.exit_code == 0
    assert mock_log.call_args.kwargs["operation"] == "scale_tkc_cluster"
    assert mock_log.call_args.kwargs["result"] == "success"


def test_cli_audit_failure_warns_but_does_not_block(capsys):
    from vmware_vks.cli import _audit_cli

    with patch(
        "vmware_vks.notify.audit.AuditLogger",
        side_effect=OSError("disk full"),
    ):
        _audit_cli("t", "op", "res", {}, "success")  # must not raise
    assert "audit log write failed" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# #7 — nested topology lookups guarded with .get() chains
# ---------------------------------------------------------------------------

def test_get_tkc_cluster_tolerates_missing_topology_fields():
    from vmware_vks.ops.tkc import get_tkc_cluster

    api = MagicMock()
    api.get_namespaced_custom_object.return_value = {"spec": {}, "status": {}}
    with (
        patch("vmware_vks.ops.tkc._get_custom_objects_api", return_value=api),
        patch("vmware_vks.ops.tkc._resolve_tkc_version", return_value="v1beta1"),
    ):
        result = get_tkc_cluster(_mock_si(), "c1", "dev")
    assert result["worker_replicas"] is None
    assert result["control_plane_replicas"] is None
    assert result["k8s_version"] is None


def test_list_tkc_clusters_tolerates_missing_spec():
    from vmware_vks.ops.tkc import list_tkc_clusters

    api = MagicMock()
    api.list_namespaced_custom_object.return_value = {
        "items": [{"metadata": {"name": "c1", "namespace": "dev"}}]
    }
    with (
        patch("vmware_vks.ops.tkc._get_custom_objects_api", return_value=api),
        patch("vmware_vks.ops.tkc._resolve_tkc_version", return_value="v1beta1"),
    ):
        result = list_tkc_clusters(_mock_si(), namespace="dev")
    assert result["items"][0]["k8s_version"] == ""


# ---------------------------------------------------------------------------
# #8 — tkc delete uses --skip-workload-check (not the ambiguous --force)
# ---------------------------------------------------------------------------

def test_tkc_delete_flag_renamed_to_skip_workload_check():
    from typer.testing import CliRunner
    from vmware_vks.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["tkc", "delete", "--help"])
    assert result.exit_code == 0
    assert "--skip-workload-check" in result.output
    assert "--force" not in result.output


def test_namespace_delete_keeps_force_for_prompt_skip():
    from typer.testing import CliRunner
    from vmware_vks.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["namespace", "delete", "--help"])
    assert result.exit_code == 0
    assert "--force" in result.output


# ---------------------------------------------------------------------------
# #9 — SKILL.md tool-count header matches mcp.list_tools()
# ---------------------------------------------------------------------------

def test_skill_md_tool_counts_match_list_tools():
    import asyncio
    import re
    from pathlib import Path

    import vmware_vks.mcp_server.server as srv

    tools = asyncio.run(srv.mcp.list_tools())
    read = sum(1 for t in tools if t.annotations and t.annotations.readOnlyHint)
    write = len(tools) - read

    skill_md = (
        Path(__file__).resolve().parents[3]
        / "skills" / "vmware-vks" / "SKILL.md"
    ).read_text()
    m = re.search(r"## MCP Tools \((\d+) — (\d+) read, (\d+) write\)", skill_md)
    assert m, "SKILL.md MCP Tools header not found or malformed"
    assert (int(m.group(1)), int(m.group(2)), int(m.group(3))) == (
        len(tools), read, write,
    ), f"SKILL.md declares {m.groups()}, actual is ({len(tools)}, {read}, {write})"


# ---------------------------------------------------------------------------
# #10 — dead duplicate _vcenter_host removed from k8s_connection
# ---------------------------------------------------------------------------

def test_only_one_module_defines_the_vcenter_host_helper():
    """One definition, wherever it lives.

    The point of #10 was that two copies drift. The live copy has since moved
    from ``ops/supervisor`` to ``rest_session``, because the REST *session* and
    the REST *request* must address the same host or a mismatch shows up as a
    401 that looks like a token problem — so this pins the invariant (exactly
    one definition in the package) rather than the address it had in 2026-06.
    """
    import ast
    import inspect
    from pathlib import Path

    import vmware_vks

    root = Path(inspect.getfile(vmware_vks)).parent
    definers = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in (
                "vcenter_host",
                "_vcenter_host",
            ):
                definers.append(f"{path.name}:{node.name}")

    assert definers == ["rest_session.py:vcenter_host"], (
        f"expected exactly one vCenter-host helper, found {definers}"
    )
