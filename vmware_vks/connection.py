"""Connection management for vCenter (Layer 1: pyVmomi).

Layer 2 (kubernetes client) is handled separately in vmware_vks.k8s_connection.
"""
from __future__ import annotations

import atexit
import socket
import ssl
from collections.abc import Callable
from typing import TYPE_CHECKING

from vmware_vks.config import (
    CONFIG_FILE,
    AppConfig,
    ConfigError,
    TargetConfig,
    load_config,
)

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance


# ServiceInstance is a pyVmomi ManagedObject — its __setattr__ rejects any
# attribute not in its allowed list (raises "Managed object attributes are
# read-only" on pyVmomi 8.x). We keep per-connection metadata in this module
# dict, keyed by id(si). Cleared via atexit when the SI is disconnected.
# 踩坑 #32 (2026-05-19, 客户 vCenter 8.0U3 现场).
_SI_VERIFY_SSL: dict[int, bool] = {}

# Same side-store pattern for the full TargetConfig — needed by wcp_login to
# re-authenticate against POST /wcp/login (Supervisor bearer-token flow).
_SI_TARGET: dict[int, TargetConfig] = {}

# atexit cleanups for live connections, keyed by id(si) so a connection dropped
# before interpreter exit can take its handler with it.
_SI_ATEXIT: dict[int, Callable[[], None]] = {}


def _release_si(si: ServiceInstance) -> None:
    """Unregister the atexit cleanup registered for ``si``.

    Every connect() registers a cleanup that closes over si, and atexit holds
    that closure -- and therefore si -- until the process exits. A long-running
    MCP server that reconnects after each session expiry (踩坑 #40) accumulates
    one dead ServiceInstance and one handler per reconnect, and at exit runs a
    Disconnect against every session it ever opened.

    Measured before this existed: 50 evict-and-reconnect cycles left 50 handlers
    registered and all 50 evicted ServiceInstance objects still reachable, while
    the id(si) side stores stayed correctly at one entry -- the side-store
    discipline was never the leak, the registration was.

    Boundary: this covers the paths that drop a *connection* -- the eviction
    inside connect() and disconnect(). A caller that drops the whole
    ConnectionManager while it still holds connections leaks them exactly as
    before, since both atexit and this table still reach them. Unchanged rather
    than fixed: the reconnect loop is the shape that grows without bound.
    """
    fn = _SI_ATEXIT.pop(id(si), None)
    if fn is not None:
        atexit.unregister(fn)



def _evict_si_metadata(si: "ServiceInstance") -> None:
    """Drop all id(si)-keyed side-store entries for ``si``.

    Must be called whenever a connection is evicted, not just at atexit: once
    the old si is garbage-collected, a new si for a different target can reuse
    the same id() value and read stale metadata. .pop(..., None) is safe if the
    key is already absent. Keep this in sync with every id(si)-keyed dict above.
    """
    key = id(si)
    _SI_VERIFY_SSL.pop(key, None)
    _SI_TARGET.pop(key, None)
    _release_si(si)


def get_verify_ssl(si: "ServiceInstance") -> bool:
    """Return verify_ssl flag stashed by the connect() that created ``si``.

    Defaults to True (strict) if the SI was created outside this manager.
    """
    return _SI_VERIFY_SSL.get(id(si), True)


def get_target_config(si: "ServiceInstance") -> TargetConfig | None:
    """Return the TargetConfig stashed by the connect() that created ``si``.

    Returns None if the SI was created outside this manager (e.g. raw
    SmartConnect in tests). Used by wcp_login.get_wcp_token to obtain
    host/username/password for the Supervisor /wcp/login flow.
    """
    return _SI_TARGET.get(id(si))


class ConnectionManager:
    """Manages pyVmomi connections to multiple vCenter targets."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._connections: dict[str, ServiceInstance] = {}

    @classmethod
    def from_config(cls, config: AppConfig | None = None) -> ConnectionManager:
        cfg = config or load_config()
        return cls(cfg)

    def connect(self, target_name: str | None = None) -> ServiceInstance:
        target = (
            self._config.get_target(target_name)
            if target_name
            else self._config.default_target
        )
        if target.name in self._connections:
            si = self._connections[target.name]
            try:
                # Probe liveness; expired tokens can surface as a None
                # currentSession instead of raising.
                alive = si.content.sessionManager.currentSession is not None
            except Exception:
                # Any failure (NotAuthenticated, socket error, …) means the
                # cached session is unusable — drop it and reconnect below.
                alive = False
            if alive:
                return si
            # Evict stale session. Pop the id(si)-keyed side stores NOW
            # rather than waiting for atexit: once the old si is GC'd, a
            # new si for a DIFFERENT target can reuse the same id() value
            # and read stale verify_ssl/target metadata (id-reuse hazard).
            _evict_si_metadata(si)
            del self._connections[target.name]
        si = self._create_connection(target)
        self._connections[target.name] = si
        return si

    def list_targets(self) -> list[str]:
        return [t.name for t in self._config.targets]

    @staticmethod
    def _create_connection(target: TargetConfig) -> ServiceInstance:
        from pyVim.connect import Disconnect, SmartConnect

        context = None
        if not target.verify_ssl:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        # Resolve credentials BEFORE the try block. Both are properties, and
        # the missing-password one raises ConfigError — an OSError subclass the
        # handlers below would otherwise relabel as a TLS/DNS failure, burying
        # this family's most common first-run error behind the wrong remedy.
        # Read adjacently so a sidecar rotating both halves cannot split them.
        user, pwd = target.username, target.password

        try:
            si = SmartConnect(
                host=target.host,
                user=user,
                pwd=pwd,
                port=target.port,
                sslContext=context,
                disableSslCertValidation=not target.verify_ssl,
            )
        # These three carry the certificate subject, the unresolved hostname
        # and the full host:port respectively. _safe_error no longer passes
        # bare OSError through, so an agent would see only the class name —
        # translate to authored text that names the target and the setting to
        # change, and never interpolates the original exception. The raw detail
        # stays on __cause__, which only reaches the server-side log.
        except ssl.SSLError as exc:
            raise ConfigError(
                f"TLS verification failed for target '{target.name}' — set "
                f"verify_ssl: false on that target in {CONFIG_FILE} if it uses a "
                f"self-signed certificate, or install its CA on this host."
            ) from exc
        except socket.gaierror as exc:
            raise ConfigError(
                f"Could not resolve the host configured for target '{target.name}' "
                f"— check that target's 'host' value in {CONFIG_FILE} for a typo "
                f"or a DNS suffix this machine cannot resolve."
            ) from exc
        except OSError as exc:
            # ConfigError, like the two branches above it. Raising the builtin
            # ConnectionError here forced it onto the passthrough allowlist to
            # let this authored text through — and that same entry then passed
            # urllib3's own ConnectionError, whose text is
            # "HTTPSConnectionPool(host='vc.internal', port=443)". One type,
            # two provenances: an allowlist cannot tell them apart, so the
            # authored message gets its own type instead.
            raise ConfigError(
                f"Could not reach target '{target.name}' — check that the vCenter "
                f"host is up and that its 'host' and 'port' in {CONFIG_FILE} are "
                f"reachable from this machine."
            ) from exc

        # Stash verify_ssl in module dict (NOT on si — pyVmomi 8.x rejects
        # setattr on ManagedObject, see 踩坑 #32). Consumers read via
        # get_verify_ssl(si).
        _SI_VERIFY_SSL[id(si)] = target.verify_ssl
        _SI_TARGET[id(si)] = target

        def _cleanup(_si: "ServiceInstance" = si) -> None:
            _evict_si_metadata(_si)
            try:
                Disconnect(_si)
            except Exception:
                pass

        _SI_ATEXIT[id(si)] = _cleanup
        atexit.register(_cleanup)
        return si
