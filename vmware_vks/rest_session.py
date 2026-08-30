"""vSphere Automation REST session — the token the ``/api`` endpoints accept.

vCenter keeps two session stores and this skill talks to both:

* the **vSphere Web Services (SOAP)** session under ``/sdk``, opened by
  pyVmomi's ``SmartConnect``. Its key is
  ``si.content.sessionManager.currentSession.key``.
* the **vSphere Automation (REST)** session under ``/api``, minted by
  ``POST /api/session``. Its id goes in the ``vmware-api-session-id`` header.

They are not interchangeable. Handing the SOAP key to ``/api`` gets a 401 on
every call, because the Automation API is being shown an id it never issued —
which is exactly what this skill did until 2026-08-30 (VCF 9.1 hardware pass:
every REST tool 401, and the 401 reported as a permissions problem).

The contract below was verified against VMware's own implementations rather
than recalled (CLAUDE.md 踩坑 #36, the round where a whole API layer was
written from memory and half of it 404'd):

* ``POST`` to the session path with **HTTP Basic** credentials, and read the id
  out of the response body — govmomi's ``vapi/rest`` client (``Login`` sets
  basic auth, then ``req.Header.Set(SessionCookieName, id)``, where
  ``SessionCookieName = "vmware-api-session-id"``).
* under the ``/api`` prefix the body is a **bare JSON string**; the legacy
  ``/rest`` prefix wraps the same value as ``{"value": ...}`` — govmomi's vAPI
  simulator, whose ``OK`` helper adds the ``value`` envelope for ``/rest`` while
  ``StatusOK`` writes the bare value for ``/api``. Both shapes are accepted
  here: it costs one ``isinstance`` and removes a class of silent failure if a
  proxy or an older build answers in the legacy envelope.

This mirrors :mod:`vmware_vks.wcp_login`, which does the same job for the
Supervisor Kubernetes bearer token — the *other* place this family had put the
SOAP key. Credentials come from the connection manager's side store, so both
flows authenticate as the target that opened the session.

Sessions are cached per ``(host, username)`` and carry **no TTL**. vCenter's
idle timeout is a server-side setting this code cannot read, and a guessed
expiry is a number that is wrong on somebody's vCenter; the 401 handling in
``ops.supervisor._rest_request`` is the real refresh trigger — it drops the
cached id and logs in again exactly once.
"""

from __future__ import annotations

import base64
import json
import logging
import ssl
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

from vmware_vks.errors import VksApiError, connection_failure_message

_log = logging.getLogger("vmware-vks.rest-session")

#: Session-creation path, relative to the ``/api`` prefix the ops layer uses.
SESSION_PATH = "/api/session"

_LOGIN_TIMEOUT = 30

#: (host, username) -> session id. Never written to disk.
_token_cache: dict[tuple[str, str], str] = {}


def vcenter_host(si: ServiceInstance) -> str:
    """Host the pyVmomi session is connected to, without the port.

    The single definition, imported by the ops layer, so the session is always
    minted by the same server the data requests go to. Two copies of this would
    be two chances for the login and the request to address different hosts and
    produce a 401 that looks like a token problem (CLAUDE.md 形态 #6).
    """
    return si._stub.host.split(":")[0]


def invalidate_rest_session(host: str, username: str) -> None:
    """Forget the cached id for ``(host, username)`` — call on a 401."""
    _token_cache.pop((host, username), None)


def invalidate_rest_session_for_si(si: ServiceInstance) -> None:
    """Forget the cached id behind this connection."""
    from vmware_vks.connection import get_target_config

    target = get_target_config(si)
    if target is not None:
        invalidate_rest_session(vcenter_host(si), target.username)


def _extract_id(payload: object) -> str | None:
    """Pull the session id out of either response shape, or return None."""
    if isinstance(payload, str) and payload:
        return payload
    if isinstance(payload, dict):
        value = payload.get("value")
        if isinstance(value, str) and value:
            return value
    return None


def login(
    host: str,
    username: str,
    password: str,
    verify_ssl: bool = True,
    target_name: str = "",
) -> str:
    """Create a vSphere Automation session and return its id.

    Args:
        host: vCenter hostname, no port — the same host the ``/api`` requests
            will be sent to.
        username: vCenter SSO username, e.g. ``svc@vsphere.local``.
        password: that account's password.
        verify_ssl: honour the target's TLS setting, so a self-signed lab
            behaves the same here as it does for pyVmomi.
        target_name: config target these credentials came from. Named in a
            connection-failure message so the operator knows which entry in
            config.yaml to edit; the resolved host deliberately is not.

    Raises:
        VksApiError: credentials rejected, the endpoint unreachable, or a
            response this code cannot read an id out of. Every message names
            the next step.
    """
    url = f"https://{host}{SESSION_PATH}"
    ctx = ssl.create_default_context()
    if not verify_ssl:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    req = urllib.request.Request(
        url,
        data=b"",
        headers={"Authorization": f"Basic {creds}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=_LOGIN_TIMEOUT) as resp:  # nosec B310
            raw = resp.read()
    except urllib.error.HTTPError as e:
        invalidate_rest_session(host, username)
        if e.code in (401, 403):
            # The one 401 in this flow that really is about credentials: it
            # came from the login call itself, so nothing has authenticated
            # yet. Distinct from a 401 on a data call, which arrives *after*
            # this succeeded and therefore is not a password problem.
            raise VksApiError(
                f"vCenter rejected the credentials for target '{target_name or host}' "
                f"when creating a REST API session (HTTP {e.code}). Set "
                f"VMWARE_VKS_<TARGET>_PASSWORD for that target and rerun; "
                f"'vmware-vks check' reports which target is in use.",
                status_code=e.code,
            ) from e
        raise VksApiError(
            f"Could not create a vCenter REST API session (HTTP {e.code}). The "
            f"vSphere Automation endpoint answered but refused the login — run "
            f"'vmware-vks check' to confirm this target is a vCenter (an ESXi "
            f"host does not serve {SESSION_PATH}).",
            status_code=e.code,
        ) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # Authored text only: a TLS failure quotes the certificate subject and
        # a DNS failure quotes the host, and VksApiError passes through
        # _safe_error verbatim.
        raise VksApiError(
            f"Could not create a vCenter REST API session. "
            f"{connection_failure_message(e, target_name)}"
        ) from e

    try:
        payload = json.loads(raw) if raw else None
    except ValueError as e:
        raise VksApiError(
            f"{SESSION_PATH} returned a body that is not JSON — run "
            f"'vmware-vks preflight-auth' to capture the raw response and "
            f"report it with the vCenter build."
        ) from e

    session_id = _extract_id(payload)
    if not session_id:
        raise VksApiError(
            f"{SESSION_PATH} succeeded but carried no session id (got "
            f"{type(payload).__name__}) — run 'vmware-vks preflight-auth' to "
            f"capture the raw response and report it with the vCenter build."
        )

    _token_cache[(host, username)] = session_id
    return session_id


def get_rest_session_id(si: ServiceInstance) -> str:
    """Session id for the target behind this connection, creating one if needed.

    Credentials come from the connection manager's ``id(si)`` side store — the
    same route :func:`vmware_vks.wcp_login.get_wcp_token` takes, and for the
    same reason: pyVmomi 8.x refuses attribute writes on a ManagedObject
    (踩坑 #32), so per-connection metadata cannot live on ``si``.
    """
    from vmware_vks.connection import get_target_config, get_verify_ssl

    target = get_target_config(si)
    if target is None:
        raise VksApiError(
            "Connect via vmware_vks.connection.ConnectionManager, then run "
            "'vmware-vks check' to verify the target resolves from config.yaml. "
            "This ServiceInstance was not opened by ConnectionManager, so the "
            "credentials for a vCenter REST session are unavailable."
        )

    host = vcenter_host(si)
    username = target.username
    cached = _token_cache.get((host, username))
    if cached:
        return cached
    return login(
        host,
        username,
        target.password,
        verify_ssl=get_verify_ssl(si),
        target_name=target.name,
    )
