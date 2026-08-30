"""A SOAP session key is not a vSphere Automation REST session token.

Real-hardware finding, 2026-08-30 (vCenter 9.1): **every** REST tool returned
401, and the 401 was translated as *"Permission denied — verify the account has
Workload Management permissions"*. The operator was sent to fix credentials and
privileges that were already correct.

The cause is one line. ``_rest_request`` read
``si.content.sessionManager.currentSession.key`` — the key of the **SOAP**
session that pyVmomi opened against ``/sdk`` — and sent it as the
``vmware-api-session-id`` header for the **vSphere Automation** REST API under
``/api``. Those are two session stores. The Automation API mints its own id at
``POST /api/session`` (HTTP Basic on the login call, bare JSON string in the
response body under the ``/api`` prefix — the legacy ``/rest`` prefix wraps the
same value in ``{"value": ...}``), and it does not recognise a key it never
issued. Verified against govmomi's ``vapi/rest`` client and its simulator's
``OK``/``StatusOK`` split rather than from memory (CLAUDE.md 踩坑 #36).

An earlier round did fix an auth defect of exactly this shape — the Supervisor
Kubernetes bearer token, which had also been the SOAP key, now comes from
``POST /wcp/login`` in ``wcp_login.py``. That fix never reached this second
call site: no ``/api/session`` request has ever existed in this repo's history.
The REST half was not regressed; it was never done.

The second half of the finding is the mistranslation, and it is worth as much
as the first. A 401 from an endpoint whose session we just created says the
*token* was not accepted. A 403 says the account authenticated and lacks the
privilege. They send an operator to completely different places, so they must
not share a sentence. ``test_403_still_says_insufficient_permission`` is the
control: a genuinely unprivileged account must still be told so.
"""

from __future__ import annotations

import ast
import base64
import inspect
import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import vmware_vks
from vmware_vks.config import TargetConfig
from vmware_vks.connection import _SI_TARGET, _SI_VERIFY_SSL
from vmware_vks.errors import VksApiError

#: What the old code sent. Nothing may put this on the wire as a REST token.
SOAP_KEY = "52soap-session-key-do-not-use"

#: What POST /api/session returns under the /api prefix: a bare JSON string.
REST_TOKEN = "d4c3b2a1-rest-session-id"


@pytest.fixture(autouse=True)
def _clear_rest_session_cache():
    from vmware_vks import rest_session

    rest_session._token_cache.clear()
    yield
    rest_session._token_cache.clear()


def _target(password: str = "hunter2") -> TargetConfig:
    return TargetConfig(
        name="lab",
        host="vc.example.com",
        config_username="svc@vsphere.local",
        port=443,
        verify_ssl=True,
    )


def _si(monkeypatch, password: str = "hunter2"):
    """A ServiceInstance registered with the connection manager's side stores.

    Mirrors what ``ConnectionManager.connect`` does — the REST login reads the
    credentials from there, exactly as ``wcp_login.get_wcp_token`` already does.
    """
    monkeypatch.setenv("VMWARE_VKS_LAB_PASSWORD", password)
    si = MagicMock()
    si._stub.host = "vc.example.com:443"
    si.content.sessionManager.currentSession.key = SOAP_KEY
    target = _target()
    _SI_TARGET[id(si)] = target
    _SI_VERIFY_SSL[id(si)] = target.verify_ssl
    return si


def _headers(req) -> dict[str, str]:
    """Request headers, lower-cased — urllib capitalises what it stores."""
    return {k.lower(): v for k, v in req.headers.items()}


class _Resp:
    def __init__(self, payload) -> None:
        self._raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _http_error(code: int, body: bytes = b"denied") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://vc.example.com/api/x", code, "err", None, io.BytesIO(body)
    )


class _Wire:
    """Records every request and answers login vs data separately."""

    def __init__(self, data_responses):
        self.requests: list = []
        self._data = list(data_responses)
        self.login_calls = 0

    def __call__(self, req, *args, **kwargs):
        self.requests.append(req)
        if req.full_url.endswith("/api/session") and req.get_method() == "POST":
            self.login_calls += 1
            return _Resp(json.dumps(REST_TOKEN).encode())
        nxt = self._data.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return _Resp(nxt)

    @property
    def data_requests(self) -> list:
        return [r for r in self.requests if not r.full_url.endswith("/api/session")]


# ── the token itself ────────────────────────────────────────────────────────


def test_a_rest_call_logs_in_at_api_session_first(monkeypatch) -> None:
    from vmware_vks.ops.supervisor import _rest_get

    wire = _Wire([{"ok": True}])
    with patch("urllib.request.urlopen", wire):
        _rest_get(_si(monkeypatch), "/vcenter/namespace-management/clusters")

    login = wire.requests[0]
    assert login.full_url == "https://vc.example.com/api/session"
    assert login.get_method() == "POST"
    expected = base64.b64encode(b"svc@vsphere.local:hunter2").decode()
    assert _headers(login)["authorization"] == f"Basic {expected}"


def test_the_data_request_carries_the_token_the_login_returned(monkeypatch) -> None:
    from vmware_vks.ops.supervisor import _rest_get

    wire = _Wire([{"ok": True}])
    with patch("urllib.request.urlopen", wire):
        _rest_get(_si(monkeypatch), "/vcenter/namespace-management/clusters")

    sent = _headers(wire.data_requests[0])["vmware-api-session-id"]
    assert sent == REST_TOKEN


def test_the_soap_session_key_never_reaches_the_wire(monkeypatch) -> None:
    """The defect itself, stated as the thing that must not happen."""
    from vmware_vks.ops.supervisor import _rest_get

    wire = _Wire([{"ok": True}])
    with patch("urllib.request.urlopen", wire):
        _rest_get(_si(monkeypatch), "/vcenter/namespace-management/clusters")

    for req in wire.requests:
        assert SOAP_KEY not in _headers(req).values(), (
            "the pyVmomi SOAP session key was sent as a REST session id — that "
            "is the 401-on-every-tool defect"
        )


#: The only module allowed to touch the SOAP session: ``connection`` probes
#: ``currentSession`` to decide whether a cached pyVmomi session is still
#: alive, which is what that attribute is for.
_SOAP_SESSION_READERS_ALLOWED = {"connection.py"}


def _modules_reading_the_soap_session() -> set[str]:
    """Every shipped module with an ``.currentSession`` attribute read.

    Walks the AST rather than grepping, so prose explaining *why* the key is
    wrong — this test's own reason for existing, and now several docstrings —
    cannot be mistaken for the defect. Comments and strings are not code.
    """
    root = Path(inspect.getfile(vmware_vks)).parent
    found: set[str] = set()
    scanned = 0
    for path in sorted(root.rglob("*.py")):
        scanned += 1
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "currentSession":
                found.add(path.name)
    # Form #1: a scan over a mistyped root returns an empty set and reads as a
    # pass. The package has more than a handful of modules; if this is small,
    # the scan is broken, not the code clean.
    assert scanned > 5, f"only {scanned} modules scanned under {root} — scan is broken"
    return found


def test_only_the_connection_layer_reads_the_soap_session_key() -> None:
    """Belt to the wire test's braces: the attribute read must be gone.

    A wire assertion only covers the paths a test exercises, and only the ones
    it thought to exercise. This covers the package.
    """
    offenders = _modules_reading_the_soap_session() - _SOAP_SESSION_READERS_ALLOWED
    assert not offenders, (
        f"{sorted(offenders)} read the pyVmomi SOAP session key. The vSphere "
        "Automation REST API does not accept it and answers 401 to every call; "
        "get a session id from vmware_vks.rest_session instead."
    )


def test_the_soap_session_scan_can_actually_see_a_reader() -> None:
    """Positive control for the scan above.

    ``connection.py`` really does read ``currentSession``. If the walk stops
    finding it, the test above has become a green light that checks nothing.
    """
    assert "connection.py" in _modules_reading_the_soap_session()


def test_the_token_is_reused_across_calls(monkeypatch) -> None:
    """One login per (host, user), not one per request."""
    from vmware_vks.ops.supervisor import _rest_get

    si = _si(monkeypatch)
    wire = _Wire([{"a": 1}, {"b": 2}, {"c": 3}])
    with patch("urllib.request.urlopen", wire):
        _rest_get(si, "/vcenter/namespaces/instances")
        _rest_get(si, "/vcenter/namespaces/instances")
        _rest_get(si, "/vcenter/namespaces/instances")

    assert wire.login_calls == 1
    assert len(wire.data_requests) == 3


def test_a_rest_prefixed_value_wrapper_is_also_accepted(monkeypatch) -> None:
    """``{"value": id}`` is the legacy ``/rest`` shape; tolerate it.

    Cheap, and it removes a whole class of silent failure if a proxy or an
    older build answers ``/api/session`` in the legacy envelope.
    """
    from vmware_vks.rest_session import login

    with patch("urllib.request.urlopen", lambda *a, **k: _Resp({"value": REST_TOKEN})):
        assert login("vc.example.com", "u", "p") == REST_TOKEN


def test_a_session_response_of_an_unusable_shape_is_a_teaching_error() -> None:
    from vmware_vks.rest_session import login

    with (
        patch("urllib.request.urlopen", lambda *a, **k: _Resp({"nope": 1})),
        pytest.raises(VksApiError) as exc,
    ):
        login("vc.example.com", "u", "p")
    assert "preflight-auth" in str(exc.value)


def test_a_session_without_connection_metadata_names_the_fix() -> None:
    """A raw SmartConnect has no credentials on file; say so, do not 401."""
    from vmware_vks.rest_session import get_rest_session_id

    si = MagicMock()
    si._stub.host = "vc.example.com:443"
    with pytest.raises(VksApiError) as exc:
        get_rest_session_id(si)
    assert "ConnectionManager" in str(exc.value)


# ── telling 401 from 403 ────────────────────────────────────────────────────


def test_a_401_on_the_data_call_relogs_in_and_retries_once(monkeypatch) -> None:
    from vmware_vks.ops.supervisor import _rest_get

    wire = _Wire([_http_error(401), {"ok": True}])
    with patch("urllib.request.urlopen", wire):
        out = _rest_get(_si(monkeypatch), "/vcenter/namespaces/instances")

    assert out == {"ok": True}
    assert wire.login_calls == 2, "the rejected session id must not be reused"
    assert len(wire.data_requests) == 2


def test_a_401_is_not_retried_forever(monkeypatch) -> None:
    from vmware_vks.ops.supervisor import _rest_get

    wire = _Wire([_http_error(401), _http_error(401)])
    with patch("urllib.request.urlopen", wire), pytest.raises(
        VksApiError
    ):
        _rest_get(_si(monkeypatch), "/vcenter/namespaces/instances")

    assert wire.login_calls == 2
    assert len(wire.data_requests) == 2


def test_a_persistent_401_does_not_blame_the_operators_privileges(
    monkeypatch,
) -> None:
    """The mistranslation, stated as the thing that must not be said."""
    from vmware_vks.ops.supervisor import _rest_get

    wire = _Wire([_http_error(401), _http_error(401)])
    with patch("urllib.request.urlopen", wire), pytest.raises(
        VksApiError
    ) as exc:
        _rest_get(_si(monkeypatch), "/vcenter/namespaces/instances")

    msg = str(exc.value)
    assert "Permission denied" not in msg
    assert "Workload Management permissions" not in msg
    assert "session" in msg.lower(), (
        "a 401 after a successful login is about the session token, and the "
        "message has to say so or the operator goes and resets a good password"
    )
    assert exc.value.status_code == 401


def test_403_still_says_insufficient_permission(monkeypatch) -> None:
    """Control: an unprivileged account must still be told it is unprivileged.

    A fix that relabels every 401 as "wrong token" is the same defect pointing
    the other way.
    """
    from vmware_vks.ops.supervisor import _rest_get

    wire = _Wire([_http_error(403)])
    with patch("urllib.request.urlopen", wire), pytest.raises(
        VksApiError
    ) as exc:
        _rest_get(_si(monkeypatch), "/vcenter/namespaces/instances")

    msg = str(exc.value)
    assert "Permission denied" in msg
    assert "Workload Management" in msg
    assert exc.value.status_code == 403
    assert wire.login_calls == 1, "a 403 is not a session problem; do not re-login"


def test_a_403_is_not_retried(monkeypatch) -> None:
    from vmware_vks.ops.supervisor import _rest_get

    wire = _Wire([_http_error(403)])
    with patch("urllib.request.urlopen", wire), pytest.raises(
        VksApiError
    ):
        _rest_get(_si(monkeypatch), "/vcenter/namespaces/instances")
    assert len(wire.data_requests) == 1


def test_rejected_credentials_at_login_name_the_password_env_var(
    monkeypatch,
) -> None:
    """The one 401 that *is* about credentials: the login call itself."""
    from vmware_vks.ops.supervisor import _rest_get

    def _deny(req, *a, **k):
        raise _http_error(401)

    with patch("urllib.request.urlopen", _deny), pytest.raises(
        VksApiError
    ) as exc:
        _rest_get(_si(monkeypatch), "/vcenter/namespaces/instances")

    msg = str(exc.value)
    assert "VMWARE_VKS_" in msg or "_PASSWORD" in msg
    assert exc.value.status_code == 401


def test_a_write_is_not_retried_on_401_more_than_once(monkeypatch) -> None:
    """Re-auth is safe to repeat; the write behind it is not.

    A POST that got a 401 never reached the resource, so replaying it once with
    a fresh session is safe — but only once.
    """
    from vmware_vks.ops.supervisor import _rest_post

    wire = _Wire([_http_error(401), _http_error(401)])
    with patch("urllib.request.urlopen", wire), pytest.raises(
        VksApiError
    ):
        _rest_post(_si(monkeypatch), "/vcenter/namespaces/instances", {"x": 1})

    assert len(wire.data_requests) == 2
