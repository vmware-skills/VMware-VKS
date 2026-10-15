"""Tests for the live /wcp/login preflight (issue #13).

These mock the network so they run in CI, but they pin the diagnostic
contract: every operational failure becomes a Step (never a traceback), and
each failure mode carries an actionable teaching message. The REAL validation
still happens when the user runs `vmware-vks preflight-auth` against their
Supervisor — these tests just guarantee the harness reports correctly.
"""
from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from vmware_vks import preflight_auth, wcp_login
from vmware_vks.config import TargetConfig


@pytest.fixture(autouse=True)
def _clear_token_cache():
    wcp_login._token_cache.clear()
    yield
    wcp_login._token_cache.clear()


def _si_with_target(verify_ssl: bool = False):
    from vmware_vks import connection

    si = MagicMock()
    si._stub.host = "vc.example.com:443"
    target = TargetConfig(
        name="lab", host="vc.example.com", config_username="admin", verify_ssl=verify_ssl
    )
    connection._SI_TARGET[id(si)] = target
    connection._SI_VERIFY_SSL[id(si)] = verify_ssl
    return si, connection


def _k8s_response(status: int):
    resp = MagicMock()
    resp.status = status
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _login_response():
    resp = MagicMock()
    resp.read.return_value = json.dumps({"session_id": "jwt-xyz"}).encode()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_preflight_all_green_end_to_end():
    si, connection = _si_with_target()
    try:
        with patch.dict("os.environ", {"VMWARE_VKS_LAB_PASSWORD": "pw"}), patch(
            "vmware_vks.preflight_auth._connect_step", return_value=(si, preflight_auth.Step("vCenter reachable", True, "ok"))
        ), patch(
            "vmware_vks.k8s_connection._resolve_supervisor_endpoint",
            return_value="10.0.0.1:6443",
        ), patch("urllib.request.urlopen") as mock_open:
            # First call = /wcp/login, second = K8s /api probe.
            mock_open.side_effect = [_login_response(), _k8s_response(200)]
            result = preflight_auth.run_preflight_auth("lab")
    finally:
        connection._SI_TARGET.pop(id(si), None)
        connection._SI_VERIFY_SSL.pop(id(si), None)

    assert result.passed
    names = [s.name for s in result.steps]
    assert names == [
        "vCenter reachable",
        "POST /wcp/login",
        "Parse session_id",
        "Supervisor K8s API",
    ]
    # The K8s probe must have carried the bearer token from /wcp/login.
    probe_req = mock_open.call_args_list[1][0][0]
    assert probe_req.get_header("Authorization") == "Bearer jwt-xyz"
    assert probe_req.full_url == "https://10.0.0.1:6443/api"


def test_preflight_stops_at_connect_failure():
    with patch(
        "vmware_vks.preflight_auth._connect_step",
        return_value=(None, preflight_auth.Step("vCenter reachable", False, "boom")),
    ):
        result = preflight_auth.run_preflight_auth("lab")
    assert not result.passed
    assert len(result.steps) == 1
    assert result.steps[0].name == "vCenter reachable"


def test_preflight_404_on_login_gives_capture_the_path_hint():
    si, connection = _si_with_target()
    err = urllib.error.HTTPError(
        "https://vc/wcp/login", 404, "Not Found", None, io.BytesIO(b"")
    )
    try:
        with patch.dict("os.environ", {"VMWARE_VKS_LAB_PASSWORD": "pw"}), patch(
            "vmware_vks.preflight_auth._connect_step",
            return_value=(si, preflight_auth.Step("vCenter reachable", True, "ok")),
        ), patch(
            "vmware_vks.k8s_connection._resolve_supervisor_endpoint",
            return_value="10.0.0.1:6443",
        ), patch("urllib.request.urlopen", side_effect=err):
            result = preflight_auth.run_preflight_auth("lab")
    finally:
        connection._SI_TARGET.pop(id(si), None)
        connection._SI_VERIFY_SSL.pop(id(si), None)

    assert not result.passed
    login = next(s for s in result.steps if s.name == "POST /wcp/login")
    assert not login.ok
    assert "404" in login.detail
    assert "endpoint differs" in login.detail
    assert "issue #13" in login.detail
    assert "supervisor_host" in login.detail
    # No K8s probe step once login fails.
    assert "Supervisor K8s API" not in [s.name for s in result.steps]


def test_preflight_401_on_k8s_probe_flags_bad_token():
    si, connection = _si_with_target()
    err = urllib.error.HTTPError(
        "https://10.0.0.1:6443/api", 401, "Unauthorized", None, io.BytesIO(b"")
    )
    try:
        with patch.dict("os.environ", {"VMWARE_VKS_LAB_PASSWORD": "pw"}), patch(
            "vmware_vks.preflight_auth._connect_step",
            return_value=(si, preflight_auth.Step("vCenter reachable", True, "ok")),
        ), patch(
            "vmware_vks.k8s_connection._resolve_supervisor_endpoint",
            return_value="10.0.0.1:6443",
        ), patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = [_login_response(), err]
            result = preflight_auth.run_preflight_auth("lab")
    finally:
        connection._SI_TARGET.pop(id(si), None)
        connection._SI_VERIFY_SSL.pop(id(si), None)

    assert not result.passed
    probe = next(s for s in result.steps if s.name == "Supervisor K8s API")
    assert not probe.ok
    assert "401" in probe.detail
    assert "did NOT authenticate" in probe.detail


def test_preflight_403_on_k8s_probe_counts_as_authenticated():
    # 403 = RBAC denied version discovery, but the token IS valid. Must pass.
    si, connection = _si_with_target()
    err = urllib.error.HTTPError(
        "https://10.0.0.1:6443/api", 403, "Forbidden", None, io.BytesIO(b"")
    )
    try:
        with patch.dict("os.environ", {"VMWARE_VKS_LAB_PASSWORD": "pw"}), patch(
            "vmware_vks.preflight_auth._connect_step",
            return_value=(si, preflight_auth.Step("vCenter reachable", True, "ok")),
        ), patch(
            "vmware_vks.k8s_connection._resolve_supervisor_endpoint",
            return_value="10.0.0.1:6443",
        ), patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = [_login_response(), err]
            result = preflight_auth.run_preflight_auth("lab")
    finally:
        connection._SI_TARGET.pop(id(si), None)
        connection._SI_VERIFY_SSL.pop(id(si), None)

    assert result.passed
    probe = next(s for s in result.steps if s.name == "Supervisor K8s API")
    assert probe.ok
    assert "403" in probe.detail


def test_preflight_never_raises_on_endpoint_resolution_failure():
    # Login resolves the endpoint first; with supervisor_host set it does not,
    # so the probe is the step that meets the resolution failure.
    from vmware_vks import connection

    si, connection = _si_with_target()
    connection._SI_TARGET[id(si)] = TargetConfig(
        name="lab", host="vc.example.com", config_username="admin",
        verify_ssl=False, supervisor_host="10.0.0.1",
    )
    try:
        with patch.dict("os.environ", {"VMWARE_VKS_LAB_PASSWORD": "pw"}), patch(
            "vmware_vks.preflight_auth._connect_step",
            return_value=(si, preflight_auth.Step("vCenter reachable", True, "ok")),
        ), patch(
            "vmware_vks.k8s_connection._resolve_supervisor_endpoint",
            side_effect=RuntimeError("no running Supervisor"),
        ), patch("urllib.request.urlopen", return_value=_login_response()):
            result = preflight_auth.run_preflight_auth("lab")
    finally:
        connection._SI_TARGET.pop(id(si), None)
        connection._SI_VERIFY_SSL.pop(id(si), None)

    assert not result.passed
    probe = next(s for s in result.steps if s.name == "Supervisor K8s API")
    assert "could not resolve" in probe.detail


def test_preflight_bypasses_token_cache():
    # A stale cached token must NOT short-circuit the live login — the whole
    # point of the preflight is to hit the wire.
    si, connection = _si_with_target()
    wcp_login._token_cache[("10.0.0.1", "admin")] = ("stale-cached", 1e18)
    try:
        with patch.dict("os.environ", {"VMWARE_VKS_LAB_PASSWORD": "pw"}), patch(
            "vmware_vks.preflight_auth._connect_step",
            return_value=(si, preflight_auth.Step("vCenter reachable", True, "ok")),
        ), patch(
            "vmware_vks.k8s_connection._resolve_supervisor_endpoint",
            return_value="10.0.0.1:6443",
        ), patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = [_login_response(), _k8s_response(200)]
            result = preflight_auth.run_preflight_auth("lab")
            # login endpoint was actually called despite the cache entry
            login_req = mock_open.call_args_list[0][0][0]
            assert login_req.full_url == "https://10.0.0.1/wcp/login"
    finally:
        connection._SI_TARGET.pop(id(si), None)
        connection._SI_VERIFY_SSL.pop(id(si), None)

    probe_req = mock_open.call_args_list[1][0][0]
    assert probe_req.get_header("Authorization") == "Bearer jwt-xyz"
    assert result.passed
