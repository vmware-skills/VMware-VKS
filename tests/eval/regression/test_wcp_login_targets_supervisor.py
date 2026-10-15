"""/wcp/login goes to the Supervisor API endpoint, not to vCenter (VKS#13).

2026-10-14, live VCF 9.1 (vCenter 9.1.0, Supervisor v1.32.9): POST
https://<vcenter>/wcp/login answers 404, while the same request to the
Supervisor API endpoint answers 200 {"session_id": <JWT>}, and that JWT works
as a bearer against the Supervisor API. ``get_wcp_token`` logged in at the
vCenter host from config.yaml, so every Supervisor call failed on 9.1.

The login host is the Supervisor endpoint vCenter publishes
(``api_server_cluster_endpoint``) without any port: /wcp/login is served on
443. A target may set ``supervisor_host`` to name it explicitly, for
topologies where the published endpoint is not reachable from this machine.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from vmware_vks import wcp_login
from vmware_vks.config import TargetConfig


@pytest.fixture(autouse=True)
def _clear_token_cache():
    wcp_login._token_cache.clear()
    yield
    wcp_login._token_cache.clear()


@pytest.fixture
def si_for():
    from vmware_vks import connection

    made = []

    def _make(supervisor_host: str = ""):
        si = MagicMock()
        si._stub.host = "vc.example.com:443"
        connection._SI_TARGET[id(si)] = TargetConfig(
            name="lab", host="vc.example.com", config_username="admin",
            verify_ssl=False, supervisor_host=supervisor_host,
        )
        connection._SI_VERIFY_SSL[id(si)] = False
        made.append(si)
        return si

    yield _make
    for si in made:
        connection._SI_TARGET.pop(id(si), None)
        connection._SI_VERIFY_SSL.pop(id(si), None)


def _login_response(token: str = "jwt-abc"):
    resp = MagicMock()
    resp.read.return_value = json.dumps({"session_id": token}).encode()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


@pytest.mark.unit
@pytest.mark.parametrize(
    "published, expected",
    [
        ("10.0.0.5", "10.0.0.5"),
        ("10.0.0.5:6443", "10.0.0.5"),
        ("https://10.0.0.5:6443/", "10.0.0.5"),
        ("sup.example.com", "sup.example.com"),
        ("[fd00::5]:6443", "[fd00::5]"),
    ],
)
def test_login_goes_to_the_published_supervisor_endpoint(si_for, published, expected):
    si = si_for()
    with patch.dict("os.environ", {"VMWARE_VKS_LAB_PASSWORD": "pw"}), patch(
        "vmware_vks.k8s_connection._resolve_supervisor_endpoint", return_value=published
    ), patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value = _login_response()
        assert wcp_login.get_wcp_token(si) == "jwt-abc"

    assert mock_open.call_args[0][0].full_url == f"https://{expected}/wcp/login"


@pytest.mark.unit
def test_configured_supervisor_host_wins_without_discovery(si_for):
    si = si_for(supervisor_host="sup-vip.lab")
    with patch.dict("os.environ", {"VMWARE_VKS_LAB_PASSWORD": "pw"}), patch(
        "vmware_vks.k8s_connection._resolve_supervisor_endpoint",
        side_effect=AssertionError("discovery must not run when supervisor_host is set"),
    ), patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value = _login_response()
        wcp_login.get_wcp_token(si)

    assert mock_open.call_args[0][0].full_url == "https://sup-vip.lab/wcp/login"


@pytest.mark.unit
def test_a_401_invalidates_the_token_cached_for_the_supervisor_host(si_for):
    si = si_for()
    with patch.dict("os.environ", {"VMWARE_VKS_LAB_PASSWORD": "pw"}), patch(
        "vmware_vks.k8s_connection._resolve_supervisor_endpoint", return_value="10.0.0.5"
    ), patch("urllib.request.urlopen") as mock_open:
        mock_open.side_effect = [_login_response("first"), _login_response("second")]
        assert wcp_login.get_wcp_token(si) == "first"
        wcp_login.invalidate_wcp_token_for_si(si)
        assert wcp_login.get_wcp_token(si) == "second"


@pytest.mark.unit
def test_config_reads_supervisor_host(tmp_path):
    from vmware_vks.config import load_config

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "targets:\n"
        "  - name: lab\n    host: vc.example.com\n    supervisor_host: sup-vip.lab\n"
        "  - name: other\n    host: vc2.example.com\n",
        encoding="utf-8",
    )
    targets = {t.name: t for t in load_config(cfg).targets}

    assert targets["lab"].supervisor_host == "sup-vip.lab"
    assert targets["other"].supervisor_host == ""


@pytest.mark.unit
def test_preflight_logs_in_at_the_supervisor_and_says_where(si_for):
    from vmware_vks import preflight_auth

    si = si_for()
    with patch.dict("os.environ", {"VMWARE_VKS_LAB_PASSWORD": "pw"}), patch(
        "vmware_vks.k8s_connection._resolve_supervisor_endpoint", return_value="10.0.0.5:6443"
    ), patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value = _login_response()
        token, steps = preflight_auth._wcp_login_step(si)

    assert token == "jwt-abc"
    assert mock_open.call_args[0][0].full_url == "https://10.0.0.5/wcp/login"
    assert "10.0.0.5" in steps[0].detail
    assert "vc.example.com" not in mock_open.call_args[0][0].full_url


@pytest.mark.unit
def test_preflight_reports_an_unresolvable_supervisor_as_a_failed_step(si_for):
    from vmware_vks import preflight_auth
    from vmware_vks.errors import VksApiError

    si = si_for()
    with patch.dict("os.environ", {"VMWARE_VKS_LAB_PASSWORD": "pw"}), patch(
        "vmware_vks.k8s_connection._resolve_supervisor_endpoint",
        side_effect=VksApiError("No Supervisor cluster is in config_status RUNNING."),
    ), patch("urllib.request.urlopen") as mock_open:
        token, steps = preflight_auth._wcp_login_step(si)

    assert token is None
    assert steps[0].ok is False
    mock_open.assert_not_called()


# ── Review follow-ups (2026-10-15) ──────────────────────────────────────────


@pytest.mark.unit
def test_supervisor_host_also_drives_every_kubernetes_call(si_for):
    """Setting it only for the login would log in and then dial the unreachable address."""
    from vmware_vks import k8s_connection

    si = si_for(supervisor_host="https://sup-vip.lab/")
    with patch(
        "vmware_vks.ops.supervisor._rest_get",
        side_effect=AssertionError("no discovery when supervisor_host is set"),
    ), patch("vmware_vks.wcp_login.get_wcp_token", return_value="jwt"):
        kubeconfig = k8s_connection._build_supervisor_kubeconfig(si, "ns1")

    assert kubeconfig["clusters"][0]["cluster"]["server"] == "https://sup-vip.lab"


@pytest.mark.unit
@pytest.mark.parametrize("published", ["", "https://", "garbage!!", "10.0.0.5 :6443", "  "])
def test_an_unusable_supervisor_address_is_a_teaching_error(si_for, published):
    from vmware_vks.errors import VksApiError

    si = si_for()
    with patch.dict("os.environ", {"VMWARE_VKS_LAB_PASSWORD": "pw"}), patch(
        "vmware_vks.k8s_connection._resolve_supervisor_endpoint", return_value=published
    ), patch("urllib.request.urlopen") as mock_open:
        with pytest.raises(VksApiError, match="not a usable"):
            wcp_login.get_wcp_token(si)
    mock_open.assert_not_called()


@pytest.mark.unit
def test_a_bare_ipv6_supervisor_address_is_bracketed(si_for):
    si = si_for(supervisor_host="fd00::5")
    with patch.dict("os.environ", {"VMWARE_VKS_LAB_PASSWORD": "pw"}), patch(
        "urllib.request.urlopen"
    ) as mock_open:
        mock_open.return_value = _login_response()
        wcp_login.get_wcp_token(si)

    assert mock_open.call_args[0][0].full_url == "https://[fd00::5]/wcp/login"


@pytest.mark.unit
def test_an_unreachable_supervisor_points_at_the_supervisor_not_vcenter(si_for):
    import urllib.error

    from vmware_vks.errors import VksApiError

    si = si_for()
    with patch.dict("os.environ", {"VMWARE_VKS_LAB_PASSWORD": "pw"}), patch(
        "vmware_vks.k8s_connection._resolve_supervisor_endpoint", return_value="10.0.0.5"
    ), patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")):
        with pytest.raises(VksApiError) as excinfo:
            wcp_login.get_wcp_token(si)

    message = str(excinfo.value)
    assert "Supervisor" in message and "supervisor_host" in message
    assert "published by vCenter" in message
    assert "10.0.0.5" not in message


@pytest.mark.unit
def test_a_malformed_url_error_is_a_vks_error_not_a_raw_exception(si_for):
    import http.client

    from vmware_vks.errors import VksApiError

    si = si_for()
    with patch.dict("os.environ", {"VMWARE_VKS_LAB_PASSWORD": "pw"}), patch(
        "vmware_vks.k8s_connection._resolve_supervisor_endpoint", return_value="10.0.0.5"
    ), patch("urllib.request.urlopen", side_effect=http.client.InvalidURL("bad")):
        with pytest.raises(VksApiError):
            wcp_login.get_wcp_token(si)


@pytest.mark.unit
def test_invalidation_with_an_unresolvable_endpoint_is_a_no_op(si_for):
    from vmware_vks.errors import VksApiError

    si = si_for()
    with patch(
        "vmware_vks.k8s_connection._resolve_supervisor_endpoint",
        side_effect=VksApiError("No Supervisor cluster is in config_status RUNNING."),
    ):
        wcp_login.invalidate_wcp_token_for_si(si)  # must not raise


@pytest.mark.unit
@pytest.mark.parametrize(
    "supervisor_host, source",
    [("", "published by vCenter"), ("sup-vip.lab", "supervisor_host in config.yaml")],
)
def test_preflight_names_where_the_login_host_came_from(si_for, supervisor_host, source):
    import io
    import urllib.error

    from vmware_vks import preflight_auth

    si = si_for(supervisor_host=supervisor_host)
    with patch.dict("os.environ", {"VMWARE_VKS_LAB_PASSWORD": "pw"}), patch(
        "vmware_vks.k8s_connection._resolve_supervisor_endpoint", return_value="10.0.0.5"
    ), patch("urllib.request.urlopen") as mock_open:
        mock_open.return_value = _login_response()
        _, ok_steps = preflight_auth._wcp_login_step(si)
        mock_open.side_effect = urllib.error.HTTPError(
            "https://x/wcp/login", 404, "Not Found", None, io.BytesIO(b"")
        )
        _, failed_steps = preflight_auth._wcp_login_step(si)

    assert source in ok_steps[0].detail
    assert source in failed_steps[0].detail


@pytest.mark.unit
def test_preflight_resolution_failure_keeps_the_resolver_remedy(si_for):
    from vmware_vks import preflight_auth
    from vmware_vks.errors import VksApiError

    si = si_for()
    with patch(
        "vmware_vks.k8s_connection._resolve_supervisor_endpoint",
        side_effect=VksApiError("No Supervisor cluster is in config_status RUNNING."),
    ):
        _, steps = preflight_auth._wcp_login_step(si)

    assert "RUNNING" in steps[0].detail
    assert "supervisor_host" not in steps[0].detail


@pytest.mark.unit
def test_supervisor_host_port_is_kept_for_kubernetes_and_dropped_for_login(si_for):
    from vmware_vks import k8s_connection

    si = si_for(supervisor_host="sup-vip.lab:6443")
    with patch.dict("os.environ", {"VMWARE_VKS_LAB_PASSWORD": "pw"}), patch(
        "urllib.request.urlopen"
    ) as mock_open:
        mock_open.return_value = _login_response()
        kubeconfig = k8s_connection._build_supervisor_kubeconfig(si, "ns1")

    assert kubeconfig["clusters"][0]["cluster"]["server"] == "https://sup-vip.lab:6443"
    assert mock_open.call_args[0][0].full_url == "https://sup-vip.lab/wcp/login"


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["sup:abc", "sup:99999", "10.0.0.5:abc", "sup:0", "a..b"])
def test_a_bad_supervisor_host_is_one_teaching_error_on_both_paths(si_for, bad):
    from vmware_vks import k8s_connection
    from vmware_vks.errors import VksApiError

    si = si_for(supervisor_host=bad)
    with patch.dict("os.environ", {"VMWARE_VKS_LAB_PASSWORD": "pw"}), patch(
        "urllib.request.urlopen"
    ) as mock_open:
        with pytest.raises(VksApiError, match="not a usable"):
            wcp_login.get_wcp_token(si)
        with pytest.raises(VksApiError, match="not a usable"):
            k8s_connection._build_supervisor_kubeconfig(si, "ns1")
    mock_open.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    "value, expected",
    [("sup.example.com.", "sup.example.com"), ("my_sup.lab", "my_sup.lab"), ("SUP.Lab", "sup.lab")],
)
def test_real_world_hostnames_are_accepted(value, expected):
    assert wcp_login._host_only(value, "test") == expected
