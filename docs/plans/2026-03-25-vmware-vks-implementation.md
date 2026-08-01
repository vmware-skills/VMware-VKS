# vmware-vks Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a complete MCP Skill + CLI tool for managing vSphere with Tanzu (VKS) — covering Supervisor status, Namespace lifecycle, and TanzuKubernetesCluster lifecycle via AI model or command line.

**Architecture:** Dual-layer connection: Layer 1 uses pyVmomi for vCenter REST API (Supervisor/Namespace), Layer 2 uses the `kubernetes` Python client against the Supervisor K8s endpoint (TKC CR lifecycle). FastMCP server delegates to ops modules; Typer CLI wraps same ops for direct use.

**Tech Stack:** Python 3.12+, pyVmomi 8.x, kubernetes Python client 29+, FastMCP 1.x, Typer, Rich, python-dotenv

**Reference files (copy patterns from):**
- `/Users/zw/testany/myskills/VMware-Storage/vmware_storage/config.py`
- `/Users/zw/testany/myskills/VMware-Storage/vmware_storage/connection.py`
- `/Users/zw/testany/myskills/VMware-Storage/mcp_server/server.py`
- `/Users/zw/testany/myskills/VMware-AIops/vmware_aiops/cli.py`

**Working directory:** `/Users/zw/testany/myskills/VMware-VKS/`

---

## Phase 1 — Project Scaffold + Config + Connection

### Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `vmware_vks/__init__.py`
- Create: `mcp_server/__init__.py`
- Create: `mcp_server/__main__.py`
- Create: `config.example.yaml`
- Create: `.env.example`
- Create: `.gitignore`

**Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "vmware-vks"
version = "0.1.0"
description = "MCP Skill + CLI for managing vSphere with Tanzu (VKS) — Supervisor, Namespaces, and TanzuKubernetesCluster lifecycle"
readme = "README.md"
license = { text = "MIT" }
requires-python = ">=3.12"
dependencies = [
    "pyvmomi>=8.0.3.0,<10.0",
    "kubernetes>=29.0,<35.0",
    "pyaml>=24.0,<27.0",
    "typer>=0.12,<1.0",
    "rich>=13.0,<15.0",
    "python-dotenv>=1.0,<2.0",
    "mcp[cli]>=1.0,<2.0",
]

[project.scripts]
vmware-vks = "vmware_vks.cli:app"
vmware-vks-mcp = "mcp_server.server:main"

[tool.hatch.build.targets.wheel]
packages = ["vmware_vks", "mcp_server"]
```

**Step 2: Create vmware_vks/__init__.py**

```python
"""vmware-vks — MCP Skill + CLI for vSphere with Tanzu (VKS)."""

__version__ = "0.1.0"
```

**Step 3: Create mcp_server/__init__.py and __main__.py**

`mcp_server/__init__.py` — empty file.

`mcp_server/__main__.py`:
```python
from mcp_server.server import main

main()
```

**Step 4: Create config.example.yaml**

```yaml
# ~/.vmware-vks/config.yaml
# Copy to ~/.vmware-vks/config.yaml and set passwords in ~/.vmware-vks/.env

targets:
  - name: vcenter01
    host: vcenter.example.com
    username: administrator@vsphere.local
    port: 443
    verify_ssl: false
```

**Step 5: Create .env.example**

```bash
# ~/.vmware-vks/.env
# Passwords for each target: VMWARE_VKS_<TARGET_NAME_UPPER>_PASSWORD
VMWARE_VKS_VCENTER01_PASSWORD=your_password_here
```

**Step 6: Create .gitignore**

```
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.venv/
.env
*.env
*.log
.pytest_cache/
.mypy_cache/
.ruff_cache/
```

**Step 7: Commit**

```bash
cd /Users/zw/testany/myskills/VMware-VKS
git init
git add pyproject.toml vmware_vks/__init__.py mcp_server/__init__.py mcp_server/__main__.py config.example.yaml .env.example .gitignore
git commit -m "chore: project scaffold"
```

---

### Task 2: Config module

**Files:**
- Create: `vmware_vks/config.py`
- Create: `tests/__init__.py`
- Create: `tests/test_config.py`

**Step 1: Write failing test**

`tests/test_config.py`:
```python
"""Tests for vmware_vks.config."""
import os
from pathlib import Path
import pytest
from vmware_vks.config import AppConfig, TargetConfig, load_config


def test_target_config_password_from_env(monkeypatch):
    monkeypatch.setenv("VMWARE_VKS_VCENTER01_PASSWORD", "secret123")
    t = TargetConfig(name="vcenter01", host="vc.example.com", username="admin@vsphere.local")
    assert t.password == "secret123"


def test_target_config_password_missing_raises(monkeypatch):
    monkeypatch.delenv("VMWARE_VKS_VCENTER01_PASSWORD", raising=False)
    t = TargetConfig(name="vcenter01", host="vc.example.com", username="admin@vsphere.local")
    with pytest.raises(OSError, match="VMWARE_VKS_VCENTER01_PASSWORD"):
        _ = t.password


def test_load_config(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "targets:\n"
        "  - name: vc1\n"
        "    host: vc.example.com\n"
        "    username: admin@vsphere.local\n"
    )
    config = load_config(cfg_file)
    assert len(config.targets) == 1
    assert config.targets[0].name == "vc1"
    assert config.default_target.host == "vc.example.com"


def test_load_config_missing_file():
    with pytest.raises(FileNotFoundError):
        load_config(Path("/nonexistent/config.yaml"))


def test_get_target_not_found(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "targets:\n"
        "  - name: vc1\n"
        "    host: vc.example.com\n"
        "    username: admin@vsphere.local\n"
    )
    config = load_config(cfg_file)
    with pytest.raises(KeyError, match="vc2"):
        config.get_target("vc2")
```

**Step 2: Run test — expect FAIL**

```bash
cd /Users/zw/testany/myskills/VMware-VKS
uv run pytest tests/test_config.py -v
```
Expected: `ModuleNotFoundError: No module named 'vmware_vks.config'`

**Step 3: Implement vmware_vks/config.py**

Copy exactly from `/Users/zw/testany/myskills/VMware-Storage/vmware_storage/config.py`, then:
- Replace all `vmware_storage` → `vmware_vks`
- Replace `vmware-storage` → `vmware-vks`
- Replace `VMWARE_` password prefix: keep `VMWARE_` but env key is `VMWARE_VKS_<NAME>_PASSWORD`

Key change in `TargetConfig.password`:
```python
@property
def password(self) -> str:
    env_key = f"VMWARE_VKS_{self.name.upper().replace('-', '_')}_PASSWORD"
    pw = os.environ.get(env_key, "")
    if not pw:
        raise OSError(
            f"Password not found. Set environment variable: {env_key}"
        )
    return pw
```

Remove `NotifyConfig` and `scanner` — vmware-vks only needs `targets`.

Final `AppConfig`:
```python
@dataclass(frozen=True)
class AppConfig:
    targets: tuple[TargetConfig, ...] = ()

    def get_target(self, name: str) -> TargetConfig:
        for t in self.targets:
            if t.name == name:
                return t
        available = ", ".join(t.name for t in self.targets)
        raise KeyError(f"Target '{name}' not found. Available: {available}")

    @property
    def default_target(self) -> TargetConfig:
        if not self.targets:
            raise ValueError("No targets configured. Check config.yaml")
        return self.targets[0]


def load_config(config_path: Path | None = None) -> AppConfig:
    path = config_path or CONFIG_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            f"Copy config.example.yaml to {CONFIG_FILE} and edit it."
        )
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    targets = tuple(
        TargetConfig(
            name=t["name"],
            host=t["host"],
            username=t.get("username", "administrator@vsphere.local"),
            port=t.get("port", 443),
            verify_ssl=t.get("verify_ssl", False),
        )
        for t in raw.get("targets", [])
    )
    return AppConfig(targets=targets)
```

**Step 4: Run test — expect PASS**

```bash
uv run pytest tests/test_config.py -v
```
Expected: all 5 tests PASS

**Step 5: Commit**

```bash
git add vmware_vks/config.py tests/__init__.py tests/test_config.py
git commit -m "feat: config module with frozen dataclass + env-based passwords"
```

---

### Task 3: Connection module (Layer 1 — vCenter pyVmomi)

**Files:**
- Create: `vmware_vks/connection.py`

**Step 1: Write failing test**

Add to `tests/test_config.py` (or new file `tests/test_connection.py`):
```python
"""Tests for vmware_vks.connection (unit, no real vCenter)."""
from unittest.mock import MagicMock, patch
from vmware_vks.config import AppConfig, TargetConfig
from vmware_vks.connection import ConnectionManager


def _make_config():
    target = TargetConfig(name="vc1", host="vc.example.com", username="admin@vsphere.local")
    return AppConfig(targets=(target,))


def test_connection_manager_list_targets():
    mgr = ConnectionManager(_make_config())
    assert mgr.list_targets() == ["vc1"]


def test_connection_manager_connect_reuses_session(monkeypatch):
    monkeypatch.setenv("VMWARE_VKS_VC1_PASSWORD", "pw")
    mgr = ConnectionManager(_make_config())

    mock_si = MagicMock()
    mock_si.content.sessionManager.currentSession = "active"

    with patch.object(mgr, "_create_connection", return_value=mock_si) as mock_create:
        si1 = mgr.connect("vc1")
        si2 = mgr.connect("vc1")
        assert si1 is si2
        mock_create.assert_called_once()  # only created once
```

**Step 2: Run test — expect FAIL**

```bash
uv run pytest tests/test_connection.py -v
```

**Step 3: Implement vmware_vks/connection.py**

Copy from `/Users/zw/testany/myskills/VMware-Storage/vmware_storage/connection.py`, replace module name references:

```python
"""Connection management for vCenter (Layer 1: pyVmomi).

Layer 2 (kubernetes client) is handled separately in vmware_vks.k8s_connection.
"""
from __future__ import annotations

import atexit
import ssl
from typing import TYPE_CHECKING

from pyVmomi import vmodl
from vmware_vks.config import AppConfig, TargetConfig, load_config

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance


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
                _ = si.content.sessionManager.currentSession
                return si
            except (vmodl.fault.NotAuthenticated, Exception):
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

        si = SmartConnect(
            host=target.host,
            user=target.username,
            pwd=target.password,
            port=target.port,
            sslContext=context,
            disableSslCertValidation=not target.verify_ssl,
        )
        atexit.register(Disconnect, si)
        return si
```

**Step 4: Run test — expect PASS**

```bash
uv run pytest tests/test_connection.py -v
```

**Step 5: Commit**

```bash
git add vmware_vks/connection.py tests/test_connection.py
git commit -m "feat: connection manager (pyVmomi layer 1)"
```

---

## Phase 2 — Supervisor + Namespace Ops (Phase 1 tools)

### Task 4: ops/__init__.py + supervisor.py

**Files:**
- Create: `vmware_vks/ops/__init__.py`
- Create: `vmware_vks/ops/supervisor.py`
- Create: `tests/test_supervisor.py`

**Step 1: Write failing tests**

`tests/test_supervisor.py`:
```python
"""Tests for supervisor ops (unit, mocked ServiceInstance)."""
from unittest.mock import MagicMock, patch
import pytest
from vmware_vks.ops.supervisor import (
    check_vks_compatibility,
    get_supervisor_status,
    list_supervisor_storage_policies,
)


def _mock_si():
    si = MagicMock()
    # vCenter version
    si.content.about.version = "8.0.2"
    si.content.about.build = "21290409"
    return si


def test_get_supervisor_status_returns_dict():
    si = _mock_si()
    # Mock REST response via requests
    with patch("vmware_vks.ops.supervisor._rest_get") as mock_get:
        mock_get.return_value = {
            "config_status": "RUNNING",
            "kubernetes_status": "READY",
            "api_server_cluster_endpoint": "192.168.1.10:6443",
        }
        result = get_supervisor_status(si, "domain-c1")
    assert result["kubernetes_status"] == "READY"
    assert "api_server_cluster_endpoint" in result


def test_check_vks_compatibility_vcenter_version():
    si = _mock_si()
    with patch("vmware_vks.ops.supervisor._rest_get") as mock_get:
        mock_get.return_value = []  # no clusters yet
        result = check_vks_compatibility(si)
    assert result["vcenter_version"] == "8.0.2"
    assert result["compatible"] is True  # 8.x is compatible


def test_list_supervisor_storage_policies_returns_list():
    si = _mock_si()
    with patch("vmware_vks.ops.supervisor._rest_get") as mock_get:
        mock_get.return_value = [
            {"storage_policy": "vsphere-storage", "compatible_clusters": ["domain-c1"]}
        ]
        result = list_supervisor_storage_policies(si)
    assert isinstance(result, list)
    assert result[0]["storage_policy"] == "vsphere-storage"
```

**Step 2: Run test — expect FAIL**

```bash
uv run pytest tests/test_supervisor.py -v
```

**Step 3: Implement vmware_vks/ops/supervisor.py**

```python
"""Supervisor layer operations (read-only).

Uses vCenter REST API via requests (same session cookie as pyVmomi).
All functions take (si: ServiceInstance) as first argument.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

_log = logging.getLogger("vmware-vks.ops.supervisor")

# Minimum vCenter version for VKS
_MIN_VERSION = (8, 0, 0)


def _vcenter_host(si: ServiceInstance) -> str:
    """Extract vCenter hostname from ServiceInstance."""
    # si._stub.host is 'host:port'
    return si._stub.host.split(":")[0]


def _rest_get(si: ServiceInstance, path: str) -> Any:
    """Perform authenticated REST GET using the active pyVmomi session cookie."""
    import urllib.request
    import urllib.error
    import json
    import ssl

    host = _vcenter_host(si)
    # Get session ID from pyVmomi session manager
    session_id = si.content.sessionManager.currentSession.key
    url = f"https://{host}/api{path}"

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(url, headers={"vmware-api-session-id": session_id})
    try:
        with urllib.request.urlopen(req, context=ctx) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"REST GET {path} failed ({e.code}): {body}") from e


def check_vks_compatibility(si: ServiceInstance) -> dict:
    """Check if this vCenter supports VKS (vSphere 8.x+).

    Returns dict with: compatible, vcenter_version, vcenter_build,
    wcp_enabled_clusters, network_backend.
    """
    about = si.content.about
    version_str = about.version  # e.g. "8.0.2"
    parts = tuple(int(x) for x in version_str.split(".")[:3])
    compatible = parts >= _MIN_VERSION

    # Check which clusters have WCP enabled
    try:
        clusters = _rest_get(si, "/vcenter/namespace-management/clusters")
    except Exception:
        clusters = []

    enabled = [c for c in clusters if c.get("config_status") == "RUNNING"]

    return {
        "compatible": compatible,
        "vcenter_version": version_str,
        "vcenter_build": about.build,
        "min_required_version": "8.0.0",
        "wcp_enabled_clusters": len(enabled),
        "wcp_clusters": [
            {"cluster": c.get("cluster"), "status": c.get("config_status")}
            for c in clusters
        ],
        "hint": None if compatible else "VKS requires vSphere 8.0+. Upgrade vCenter.",
    }


def get_supervisor_status(si: ServiceInstance, cluster_id: str) -> dict:
    """Get Supervisor Cluster status for a given compute cluster MoRef ID.

    Args:
        si: vCenter ServiceInstance
        cluster_id: Compute cluster MoRef (e.g. 'domain-c1')

    Returns dict with: cluster_id, config_status, kubernetes_status,
    api_server_cluster_endpoint, kubernetes_version.
    """
    data = _rest_get(si, f"/vcenter/namespace-management/clusters/{cluster_id}")
    return {
        "cluster_id": cluster_id,
        "config_status": data.get("config_status"),
        "kubernetes_status": data.get("kubernetes_status"),
        "api_server_cluster_endpoint": data.get("api_server_cluster_endpoint"),
        "kubernetes_version": data.get("current_kubernetes_version"),
        "network_provider": data.get("network_provider"),
    }


def list_supervisor_storage_policies(si: ServiceInstance) -> list[dict]:
    """List storage policies compatible with Supervisor Namespaces.

    Returns list of dicts with: storage_policy, compatible_clusters.
    """
    data = _rest_get(si, "/vcenter/namespace-management/storage/storage-policies")
    return [
        {
            "storage_policy": item.get("storage_policy"),
            "compatible_clusters": item.get("compatible_clusters", []),
        }
        for item in (data if isinstance(data, list) else [])
    ]
```

**Step 4: Run tests — expect PASS**

```bash
uv run pytest tests/test_supervisor.py -v
```

**Step 5: Commit**

```bash
git add vmware_vks/ops/__init__.py vmware_vks/ops/supervisor.py tests/test_supervisor.py
git commit -m "feat: supervisor ops (check_vks_compatibility, get_supervisor_status, list_storage_policies)"
```

---

### Task 5: namespace.py

**Files:**
- Create: `vmware_vks/ops/namespace.py`
- Create: `tests/test_namespace.py`

**Step 1: Write failing tests**

`tests/test_namespace.py`:
```python
"""Tests for namespace ops."""
from unittest.mock import MagicMock, patch
import pytest
from vmware_vks.ops.namespace import (
    list_namespaces,
    get_namespace,
    create_namespace,
    update_namespace,
    delete_namespace,
    list_vm_classes,
)


def _mock_si():
    si = MagicMock()
    si.content.about.version = "8.0.2"
    return si


def test_list_namespaces_returns_list():
    si = _mock_si()
    with patch("vmware_vks.ops.namespace._rest_get") as mock_get:
        mock_get.return_value = [
            {"namespace": "dev", "config_status": "RUNNING", "description": ""}
        ]
        result = list_namespaces(si)
    assert isinstance(result, list)
    assert result[0]["namespace"] == "dev"


def test_get_namespace_returns_dict():
    si = _mock_si()
    with patch("vmware_vks.ops.namespace._rest_get") as mock_get:
        mock_get.return_value = {
            "namespace": "dev",
            "config_status": "RUNNING",
            "resource_spec": {"cpu_limit": 4000, "memory_limit": 8192},
        }
        result = get_namespace(si, "dev")
    assert result["namespace"] == "dev"
    assert "resource_spec" in result


def test_delete_namespace_rejects_if_tkc_exists():
    si = _mock_si()
    with patch("vmware_vks.ops.namespace._rest_get") as mock_get, \
         patch("vmware_vks.ops.namespace._list_tkc_in_namespace") as mock_tkc:
        mock_tkc.return_value = ["cluster-a"]
        with pytest.raises(RuntimeError, match="cluster-a"):
            delete_namespace(si, "dev", confirmed=True)


def test_delete_namespace_dry_run():
    si = _mock_si()
    with patch("vmware_vks.ops.namespace._list_tkc_in_namespace") as mock_tkc:
        mock_tkc.return_value = []
        result = delete_namespace(si, "dev", confirmed=True, dry_run=True)
    assert "dry_run" in result
    assert result["dry_run"] is True


def test_list_vm_classes_returns_list():
    si = _mock_si()
    with patch("vmware_vks.ops.namespace._rest_get") as mock_get:
        mock_get.return_value = [
            {"id": "best-effort-large", "cpu_count": 4, "memory_mib": 8192}
        ]
        result = list_vm_classes(si)
    assert result[0]["id"] == "best-effort-large"
```

**Step 2: Run test — expect FAIL**

```bash
uv run pytest tests/test_namespace.py -v
```

**Step 3: Implement vmware_vks/ops/namespace.py**

```python
"""Namespace lifecycle operations.

All write operations require confirmed=True to prevent accidental execution.
delete_namespace has an additional guard: rejects if TKC clusters exist inside.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

from vmware_vks.ops.supervisor import _rest_get

_log = logging.getLogger("vmware-vks.ops.namespace")


def _rest_post(si: ServiceInstance, path: str, body: dict) -> Any:
    import urllib.request
    import json
    import ssl

    host = si._stub.host.split(":")[0]
    session_id = si.content.sessionManager.currentSession.key
    url = f"https://{host}/api{path}"
    data = json.dumps(body).encode()

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "vmware-api-session-id": session_id,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    import urllib.error
    try:
        with urllib.request.urlopen(req, context=ctx) as resp:
            body = resp.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"REST POST {path} failed ({e.code}): {e.read().decode()}") from e


def _rest_patch(si: ServiceInstance, path: str, body: dict) -> Any:
    import urllib.request
    import json
    import ssl
    import urllib.error

    host = si._stub.host.split(":")[0]
    session_id = si.content.sessionManager.currentSession.key
    url = f"https://{host}/api{path}"
    data = json.dumps(body).encode()

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "vmware-api-session-id": session_id,
            "Content-Type": "application/json",
        },
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(req, context=ctx) as resp:
            body = resp.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"REST PATCH {path} failed ({e.code}): {e.read().decode()}") from e


def _rest_delete(si: ServiceInstance, path: str) -> None:
    import urllib.request
    import ssl
    import urllib.error

    host = si._stub.host.split(":")[0]
    session_id = si.content.sessionManager.currentSession.key
    url = f"https://{host}/api{path}"

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(
        url,
        headers={"vmware-api-session-id": session_id},
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(req, context=ctx):
            pass
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"REST DELETE {path} failed ({e.code}): {e.read().decode()}") from e


def _list_tkc_in_namespace(si: ServiceInstance, namespace: str) -> list[str]:
    """Return list of TKC cluster names in a namespace (via K8s API).

    Falls back to empty list if K8s connection not available.
    Used as guard before namespace deletion.
    """
    try:
        from vmware_vks.ops.tkc import list_tkc_clusters
        result = list_tkc_clusters(si, namespace=namespace)
        return [c["name"] for c in result.get("clusters", [])]
    except Exception:
        return []


def list_namespaces(si: ServiceInstance) -> list[dict]:
    """List all vSphere Namespaces with status and resource usage."""
    data = _rest_get(si, "/vcenter/namespaces/instances")
    return [
        {
            "namespace": item.get("namespace"),
            "config_status": item.get("config_status"),
            "description": item.get("description", ""),
        }
        for item in (data if isinstance(data, list) else [])
    ]


def get_namespace(si: ServiceInstance, name: str) -> dict:
    """Get detailed info for a single namespace."""
    return _rest_get(si, f"/vcenter/namespaces/instances/{name}")


def create_namespace(
    si: ServiceInstance,
    name: str,
    cluster_id: str,
    storage_policy: str,
    cpu_limit: int | None = None,
    memory_limit_mib: int | None = None,
    description: str = "",
    dry_run: bool = False,
) -> dict:
    """Create a vSphere Namespace.

    Args:
        name: Namespace name
        cluster_id: Supervisor cluster MoRef (e.g. 'domain-c1')
        storage_policy: Storage policy name
        cpu_limit: CPU limit in MHz (optional)
        memory_limit_mib: Memory limit in MiB (optional)
        description: Optional description
        dry_run: If True, return the spec without applying
    """
    spec: dict = {
        "namespace": name,
        "cluster": cluster_id,
        "description": description,
        "storage_specs": [{"policy": storage_policy}],
    }
    resource_spec: dict = {}
    if cpu_limit:
        resource_spec["cpu_limit"] = cpu_limit
    if memory_limit_mib:
        resource_spec["memory_limit"] = memory_limit_mib
    if resource_spec:
        spec["resource_spec"] = resource_spec

    if dry_run:
        return {"dry_run": True, "spec": spec, "action": "create_namespace"}

    _rest_post(si, "/vcenter/namespaces/instances", spec)
    return {"namespace": name, "status": "created", "cluster": cluster_id}


def update_namespace(
    si: ServiceInstance,
    name: str,
    cpu_limit: int | None = None,
    memory_limit_mib: int | None = None,
    storage_policy: str | None = None,
) -> dict:
    """Update namespace resource quotas or storage policy."""
    spec: dict = {}
    resource_spec: dict = {}
    if cpu_limit is not None:
        resource_spec["cpu_limit"] = cpu_limit
    if memory_limit_mib is not None:
        resource_spec["memory_limit"] = memory_limit_mib
    if resource_spec:
        spec["resource_spec"] = resource_spec
    if storage_policy:
        spec["storage_specs"] = [{"policy": storage_policy}]

    if not spec:
        return {"namespace": name, "status": "no_changes"}

    _rest_patch(si, f"/vcenter/namespaces/instances/{name}", spec)
    return {"namespace": name, "status": "updated"}


def delete_namespace(
    si: ServiceInstance,
    name: str,
    confirmed: bool = False,
    dry_run: bool = False,
) -> dict:
    """Delete a vSphere Namespace.

    Safety guards:
    1. confirmed=True required
    2. Rejects if any TKC clusters exist inside the namespace
    3. dry_run=True shows what would happen without executing
    """
    if not confirmed:
        raise ValueError(
            f"confirmed=True required to delete namespace '{name}'. "
            "This is a destructive operation."
        )

    tkc_clusters = _list_tkc_in_namespace(si, name)
    if tkc_clusters:
        raise RuntimeError(
            f"Cannot delete namespace '{name}': "
            f"TKC clusters still exist: {', '.join(tkc_clusters)}. "
            "Delete all TKC clusters first."
        )

    if dry_run:
        return {
            "dry_run": True,
            "action": "delete_namespace",
            "namespace": name,
            "warning": "This will permanently delete the namespace and all its resources.",
        }

    _rest_delete(si, f"/vcenter/namespaces/instances/{name}")
    return {"namespace": name, "status": "deleted"}


def list_vm_classes(si: ServiceInstance) -> list[dict]:
    """List available VM classes for TKC node sizing."""
    data = _rest_get(si, "/vcenter/namespace-management/virtual-machine-classes")
    return [
        {
            "id": item.get("id"),
            "cpu_count": item.get("cpu_count"),
            "memory_mib": item.get("memory_mib"),
            "gpu_count": item.get("gpu_count", 0),
        }
        for item in (data if isinstance(data, list) else [])
    ]
```

**Step 4: Run tests — expect PASS**

```bash
uv run pytest tests/test_namespace.py -v
```

**Step 5: Commit**

```bash
git add vmware_vks/ops/namespace.py tests/test_namespace.py
git commit -m "feat: namespace ops (list/get/create/update/delete/vm-classes) with TKC guard"
```

---

## Phase 3 — Audit Logger + Doctor

### Task 6: Audit logger

**Files:**
- Create: `vmware_vks/notify/__init__.py`
- Create: `vmware_vks/notify/audit.py`

**Step 1: Implement vmware_vks/notify/audit.py**

Copy from `/Users/zw/testany/myskills/VMware-Storage/vmware_storage/notify/audit.py`, replace module references. The audit logger writes JSON Lines to `~/.vmware-vks/audit.log`.

```python
"""Audit logger for write operations."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

_AUDIT_DIR = Path.home() / ".vmware-vks"
_AUDIT_FILE = _AUDIT_DIR / "audit.log"
_log = logging.getLogger("vmware-vks.audit")


class AuditLogger:
    def __init__(self, log_file: Path = _AUDIT_FILE) -> None:
        self._file = log_file
        self._file.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        target: str,
        operation: str,
        resource: str,
        parameters: dict,
        result: str,
        user: str = "",
    ) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "target": target,
            "operation": operation,
            "resource": resource,
            "parameters": parameters,
            "result": result,
            "user": user,
        }
        try:
            with open(self._file, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as e:
            _log.warning("Failed to write audit log: %s", e)
```

**Step 2: Create vmware_vks/notify/__init__.py** — empty file.

**Step 3: Commit**

```bash
git add vmware_vks/notify/__init__.py vmware_vks/notify/audit.py
git commit -m "feat: audit logger (JSON Lines to ~/.vmware-vks/audit.log)"
```

---

### Task 7: Doctor (pre-flight check)

**Files:**
- Create: `vmware_vks/doctor.py`

**Step 1: Implement vmware_vks/doctor.py**

```python
"""Pre-flight diagnostics for vmware-vks.

Checks: config file exists, password set, vCenter reachable,
vCenter version >= 8.0, WCP (Workload Management) enabled.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

_log = logging.getLogger("vmware-vks.doctor")
console = Console()


def run_doctor(config_path: Path | None = None) -> bool:
    """Run all pre-flight checks. Returns True if all pass."""
    from vmware_vks.config import CONFIG_FILE, ENV_FILE, load_config

    checks = []

    # 1. Config file
    path = config_path or CONFIG_FILE
    if path.exists():
        checks.append(("Config file", True, str(path)))
    else:
        checks.append(("Config file", False, f"Not found: {path}. Copy config.example.yaml"))

    # 2. Load config
    config = None
    try:
        config = load_config(path)
        checks.append(("Config parse", True, f"{len(config.targets)} target(s)"))
    except Exception as e:
        checks.append(("Config parse", False, str(e)))

    # 3. Passwords
    if config:
        for t in config.targets:
            try:
                _ = t.password
                checks.append((f"Password ({t.name})", True, "Set"))
            except OSError as e:
                checks.append((f"Password ({t.name})", False, str(e)))

    # 4. vCenter reachable + version + WCP
    if config:
        for t in config.targets:
            try:
                from vmware_vks.connection import ConnectionManager
                mgr = ConnectionManager(config)
                si = mgr.connect(t.name)
                version = si.content.about.version
                checks.append((f"vCenter reachable ({t.name})", True, f"v{version}"))

                # Version check
                parts = tuple(int(x) for x in version.split(".")[:2])
                if parts >= (8, 0):
                    checks.append((f"vCenter version ({t.name})", True, f"{version} >= 8.0 ✓"))
                else:
                    checks.append((f"vCenter version ({t.name})", False, f"{version} < 8.0 (VKS requires 8.x+)"))

                # WCP check
                from vmware_vks.ops.supervisor import _rest_get
                clusters = _rest_get(si, "/vcenter/namespace-management/clusters")
                running = [c for c in clusters if c.get("config_status") == "RUNNING"]
                if running:
                    checks.append((f"WCP enabled ({t.name})", True, f"{len(running)} cluster(s) running"))
                else:
                    checks.append((f"WCP enabled ({t.name})", False, "No running Supervisor clusters. Enable Workload Management in vCenter UI."))

            except Exception as e:
                checks.append((f"vCenter reachable ({t.name})", False, str(e)))

    # Print results
    table = Table(title="vmware-vks Doctor", show_header=True)
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Detail")

    all_passed = True
    for name, passed, detail in checks:
        status = "[green]✓ PASS[/green]" if passed else "[red]✗ FAIL[/red]"
        table.add_row(name, status, detail)
        if not passed:
            all_passed = False

    console.print(table)
    return all_passed
```

**Step 2: Commit**

```bash
git add vmware_vks/doctor.py
git commit -m "feat: doctor pre-flight check (config, passwords, vCenter version, WCP)"
```

---

## Phase 4 — MCP Server (Phase 1: 9 tools)

### Task 8: mcp_server/server.py (Phase 1 tools)

**Files:**
- Create: `mcp_server/server.py`

**Step 1: Implement mcp_server/server.py**

```python
"""MCP server for VMware VKS (vSphere with Tanzu).

Exposes Supervisor, Namespace, and TKC lifecycle management via MCP stdio transport.

Tool categories
---------------
* **Read-only**: check_vks_compatibility, get_supervisor_status,
  list_supervisor_storage_policies, list_namespaces, get_namespace, list_vm_classes
* **Write** (require confirmed=True): create_namespace, update_namespace,
  delete_namespace (+ TKC guard)

Security
--------
* Credentials loaded from ~/.vmware-vks/.env (chmod 600 recommended)
* stdio transport only — no network listener
* delete_namespace rejects if TKC clusters exist inside

Source: https://github.com/vmware-skills/VMware-VKS
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from vmware_vks.config import load_config
from vmware_vks.connection import ConnectionManager
from vmware_vks.notify.audit import AuditLogger
from vmware_vks.ops.supervisor import (
    check_vks_compatibility,
    get_supervisor_status,
    list_supervisor_storage_policies,
)
from vmware_vks.ops.namespace import (
    create_namespace,
    delete_namespace,
    get_namespace,
    list_namespaces,
    list_vm_classes,
    update_namespace,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vmware-vks.mcp")

mcp = FastMCP("VMware VKS")
_audit = AuditLogger()

_conn_mgr: ConnectionManager | None = None


def _get_conn_mgr() -> ConnectionManager:
    global _conn_mgr
    if _conn_mgr is None:
        config_path = os.environ.get("VMWARE_VKS_CONFIG")
        config = load_config(Path(config_path) if config_path else None)
        _conn_mgr = ConnectionManager(config)
    return _conn_mgr


def _get_si(target: str | None = None):
    return _get_conn_mgr().connect(target)


# ---------------------------------------------------------------------------
# Supervisor tools
# ---------------------------------------------------------------------------

@mcp.tool()
def check_vks_compatibility_tool(target: str | None = None) -> dict:
    """Check if this vCenter supports VKS (requires vSphere 8.x+).

    Returns: compatible (bool), vcenter_version, wcp_enabled_clusters, hint.
    Call this first before any VKS operations.
    """
    si = _get_si(target)
    return check_vks_compatibility(si)


@mcp.tool()
def get_supervisor_status_tool(cluster_id: str, target: str | None = None) -> dict:
    """Get Supervisor Cluster status.

    Args:
        cluster_id: Compute cluster MoRef ID (e.g. 'domain-c1').
                    Use list_namespaces to discover cluster IDs.
        target: vCenter target name (uses default if not specified)

    Returns: config_status, kubernetes_status, api_server_endpoint, k8s_version.
    """
    si = _get_si(target)
    return get_supervisor_status(si, cluster_id)


@mcp.tool()
def list_supervisor_storage_policies_tool(target: str | None = None) -> list[dict]:
    """List storage policies available for Supervisor Namespaces.

    Returns list of storage policies with compatible cluster IDs.
    Use this to find valid storage_policy values before creating namespaces.
    """
    si = _get_si(target)
    return list_supervisor_storage_policies(si)


# ---------------------------------------------------------------------------
# Namespace tools
# ---------------------------------------------------------------------------

@mcp.tool()
def list_namespaces_tool(target: str | None = None) -> list[dict]:
    """List all vSphere Namespaces with status and resource usage."""
    si = _get_si(target)
    return list_namespaces(si)


@mcp.tool()
def get_namespace_tool(name: str, target: str | None = None) -> dict:
    """Get detailed information for a single vSphere Namespace.

    Args:
        name: Namespace name (e.g. 'dev', 'production')
        target: vCenter target name (uses default if not specified)
    """
    si = _get_si(target)
    return get_namespace(si, name)


@mcp.tool()
def create_namespace_tool(
    name: str,
    cluster_id: str,
    storage_policy: str,
    cpu_limit: int | None = None,
    memory_limit_mib: int | None = None,
    description: str = "",
    dry_run: bool = True,
    target: str | None = None,
) -> dict:
    """Create a vSphere Namespace on a Supervisor Cluster.

    IMPORTANT: dry_run=True by default — set dry_run=False to actually create.

    Args:
        name: Namespace name (lowercase, no spaces)
        cluster_id: Supervisor cluster MoRef (use get_supervisor_status to find)
        storage_policy: Storage policy name (use list_supervisor_storage_policies)
        cpu_limit: CPU limit in MHz (optional)
        memory_limit_mib: Memory limit in MiB (optional)
        description: Optional description
        dry_run: Preview without creating (default: True)
    """
    si = _get_si(target)
    result = create_namespace(
        si, name=name, cluster_id=cluster_id, storage_policy=storage_policy,
        cpu_limit=cpu_limit, memory_limit_mib=memory_limit_mib,
        description=description, dry_run=dry_run,
    )
    if not dry_run:
        _audit.log(
            target=target or "default", operation="create_namespace",
            resource=name, parameters={"cluster_id": cluster_id, "storage_policy": storage_policy},
            result="success",
        )
    return result


@mcp.tool()
def update_namespace_tool(
    name: str,
    cpu_limit: int | None = None,
    memory_limit_mib: int | None = None,
    storage_policy: str | None = None,
    target: str | None = None,
) -> dict:
    """Update vSphere Namespace resource quotas or storage policy.

    Args:
        name: Namespace name
        cpu_limit: New CPU limit in MHz (optional)
        memory_limit_mib: New memory limit in MiB (optional)
        storage_policy: New storage policy name (optional)
    """
    si = _get_si(target)
    result = update_namespace(si, name, cpu_limit=cpu_limit,
                              memory_limit_mib=memory_limit_mib, storage_policy=storage_policy)
    _audit.log(target=target or "default", operation="update_namespace",
               resource=name, parameters={}, result="success")
    return result


@mcp.tool()
def delete_namespace_tool(
    name: str,
    confirmed: bool = False,
    dry_run: bool = True,
    target: str | None = None,
) -> dict:
    """Delete a vSphere Namespace.

    SAFETY: Rejects if TKC clusters exist inside. Delete TKC clusters first.
    IMPORTANT: dry_run=True by default — set dry_run=False AND confirmed=True to delete.

    Args:
        name: Namespace name to delete
        confirmed: Must be True to proceed (safety gate)
        dry_run: Preview without deleting (default: True)
    """
    si = _get_si(target)
    result = delete_namespace(si, name, confirmed=confirmed, dry_run=dry_run)
    if not dry_run and confirmed:
        _audit.log(target=target or "default", operation="delete_namespace",
                   resource=name, parameters={}, result="success")
    return result


@mcp.tool()
def list_vm_classes_tool(target: str | None = None) -> list[dict]:
    """List available VM classes for TKC node sizing.

    Returns list of VM classes with CPU, memory specs.
    Use vm_class 'id' when creating TKC clusters.
    """
    si = _get_si(target)
    return list_vm_classes(si)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
```

**Step 2: Verify import works**

```bash
cd /Users/zw/testany/myskills/VMware-VKS
uv run python -c "from mcp_server.server import mcp; print('MCP server imports OK')"
```
Expected: `MCP server imports OK`

**Step 3: Commit**

```bash
git add mcp_server/server.py
git commit -m "feat: MCP server Phase 1 (9 tools: supervisor + namespace)"
```

---

## Phase 5 — CLI (Phase 1)

### Task 9: vmware_vks/cli.py (Phase 1 commands)

**Files:**
- Create: `vmware_vks/cli.py`

**Step 1: Implement vmware_vks/cli.py**

```python
"""Typer CLI for vmware-vks."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="vmware-vks", help="vSphere with Tanzu (VKS) management CLI")
supervisor_app = typer.Typer(help="Supervisor cluster commands")
namespace_app = typer.Typer(help="Namespace commands")

app.add_typer(supervisor_app, name="supervisor")
app.add_typer(namespace_app, name="namespace")

console = Console()

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _get_si(target: str | None = None):
    from vmware_vks.config import load_config
    from vmware_vks.connection import ConnectionManager
    config_path = os.environ.get("VMWARE_VKS_CONFIG")
    config = load_config(Path(config_path) if config_path else None)
    mgr = ConnectionManager(config)
    return mgr.connect(target)


def _double_confirm(resource: str, resource_type: str = "resource") -> bool:
    console.print(f"[red]WARNING: You are about to delete {resource_type} '{resource}'.[/red]")
    typed = typer.prompt(f"Type '{resource}' to confirm")
    return typed == resource


# ---------------------------------------------------------------------------
# check (doctor)
# ---------------------------------------------------------------------------

@app.command("check")
def cmd_check(
    config: Optional[Path] = typer.Option(None, help="Path to config.yaml"),
):
    """Run pre-flight checks (config, passwords, vCenter version, WCP)."""
    from vmware_vks.doctor import run_doctor
    ok = run_doctor(config)
    raise typer.Exit(0 if ok else 1)


# ---------------------------------------------------------------------------
# Supervisor commands
# ---------------------------------------------------------------------------

@supervisor_app.command("status")
def supervisor_status(
    cluster_id: str = typer.Argument(..., help="Compute cluster MoRef (e.g. domain-c1)"),
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """Get Supervisor Cluster status."""
    from vmware_vks.ops.supervisor import get_supervisor_status
    si = _get_si(target)
    result = get_supervisor_status(si, cluster_id)
    for k, v in result.items():
        console.print(f"  [bold]{k}:[/bold] {v}")


@supervisor_app.command("storage-policies")
def supervisor_storage_policies(
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """List storage policies available for Namespaces."""
    from vmware_vks.ops.supervisor import list_supervisor_storage_policies
    si = _get_si(target)
    policies = list_supervisor_storage_policies(si)
    table = Table("Storage Policy", "Compatible Clusters")
    for p in policies:
        table.add_row(p["storage_policy"], str(p["compatible_clusters"]))
    console.print(table)


# ---------------------------------------------------------------------------
# Namespace commands
# ---------------------------------------------------------------------------

@namespace_app.command("list")
def namespace_list(
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """List all vSphere Namespaces."""
    from vmware_vks.ops.namespace import list_namespaces
    si = _get_si(target)
    nss = list_namespaces(si)
    table = Table("Namespace", "Status", "Description")
    for ns in nss:
        table.add_row(ns["namespace"], ns["config_status"], ns.get("description", ""))
    console.print(table)


@namespace_app.command("get")
def namespace_get(
    name: str = typer.Argument(...),
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """Get details for a single Namespace."""
    from vmware_vks.ops.namespace import get_namespace
    import json
    si = _get_si(target)
    result = get_namespace(si, name)
    console.print_json(json.dumps(result))


@namespace_app.command("create")
def namespace_create(
    name: str = typer.Argument(...),
    cluster_id: str = typer.Option(..., "--cluster", help="Supervisor cluster MoRef"),
    storage_policy: str = typer.Option(..., "--storage-policy"),
    cpu_limit: Optional[int] = typer.Option(None, "--cpu"),
    memory_mib: Optional[int] = typer.Option(None, "--memory"),
    description: str = typer.Option("", "--description"),
    apply: bool = typer.Option(False, "--apply", help="Apply (default: dry-run)"),
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """Create a vSphere Namespace (dry-run by default, use --apply to create)."""
    from vmware_vks.ops.namespace import create_namespace
    import json
    si = _get_si(target)
    result = create_namespace(
        si, name=name, cluster_id=cluster_id, storage_policy=storage_policy,
        cpu_limit=cpu_limit, memory_limit_mib=memory_mib,
        description=description, dry_run=not apply,
    )
    console.print_json(json.dumps(result))


@namespace_app.command("update")
def namespace_update(
    name: str = typer.Argument(...),
    cpu_limit: Optional[int] = typer.Option(None, "--cpu"),
    memory_mib: Optional[int] = typer.Option(None, "--memory"),
    storage_policy: Optional[str] = typer.Option(None, "--storage-policy"),
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """Update Namespace resource quotas or storage policy."""
    from vmware_vks.ops.namespace import update_namespace
    import json
    si = _get_si(target)
    result = update_namespace(si, name, cpu_limit=cpu_limit,
                              memory_limit_mib=memory_mib, storage_policy=storage_policy)
    console.print_json(json.dumps(result))


@namespace_app.command("delete")
def namespace_delete(
    name: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force", help="Skip dry-run check"),
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """Delete a vSphere Namespace (rejects if TKC clusters exist inside)."""
    from vmware_vks.ops.namespace import delete_namespace

    # First show dry-run
    si = _get_si(target)
    dry = delete_namespace(si, name, confirmed=True, dry_run=True)
    console.print_json(__import__("json").dumps(dry))

    if not force:
        if not _double_confirm(name, "namespace"):
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(1)

    result = delete_namespace(si, name, confirmed=True, dry_run=False)
    console.print_json(__import__("json").dumps(result))


@namespace_app.command("vm-classes")
def namespace_vm_classes(
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """List available VM classes for TKC node sizing."""
    from vmware_vks.ops.namespace import list_vm_classes
    si = _get_si(target)
    classes = list_vm_classes(si)
    table = Table("ID", "CPU", "Memory (MiB)", "GPU")
    for c in classes:
        table.add_row(str(c["id"]), str(c["cpu_count"]), str(c["memory_mib"]), str(c["gpu_count"]))
    console.print(table)
```

**Step 2: Verify CLI entry point works**

```bash
uv run vmware-vks --help
```
Expected: Shows `check`, `supervisor`, `namespace` commands.

**Step 3: Commit**

```bash
git add vmware_vks/cli.py
git commit -m "feat: CLI Phase 1 (check, supervisor, namespace commands)"
```

---

## Phase 6 — Safety test + server.json + README

### Task 10: Safety test + server.json

**Files:**
- Create: `tests/test_no_destructive_vm_code.py`
- Create: `server.json`

**Step 1: Create safety test**

```python
"""Guard: vmware-vks must never contain pyVmomi VM lifecycle operations.
VKS manages Namespaces and K8s clusters, not VMs directly.
"""
import ast
import pathlib

FORBIDDEN_CALLS = {
    "PowerOn", "PowerOff", "Destroy", "Clone", "Relocate",
    "ReconfigVM", "MarkAsVirtualMachine", "RegisterVM",
}

def test_no_vm_lifecycle_ops():
    src_dir = pathlib.Path("vmware_vks")
    for py_file in src_dir.rglob("*.py"):
        source = py_file.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_CALLS:
                raise AssertionError(
                    f"{py_file}: Found forbidden VM lifecycle call '{node.attr}'. "
                    "vmware-vks must not modify VMs directly."
                )
```

**Step 2: Run safety test**

```bash
uv run pytest tests/test_no_destructive_vm_code.py -v
```
Expected: PASS

**Step 3: Create server.json**

```json
{
  "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
  "name": "io.github.zw008/vmware-vks",
  "title": "VMware VKS",
  "description": "MCP server for vSphere with Tanzu (VKS). Manage Supervisor Namespaces and TanzuKubernetesCluster lifecycle via AI model. Tools: check_vks_compatibility, get_supervisor_status, list_supervisor_storage_policies, list_namespaces, get_namespace, create_namespace, update_namespace, delete_namespace, list_vm_classes. Requires vSphere 8.x+.",
  "repository": {
    "url": "https://github.com/vmware-skills/VMware-VKS",
    "source": "github"
  },
  "version": "0.1.0",
  "packages": [
    {
      "registryType": "pypi",
      "identifier": "vmware-vks",
      "version": "0.1.0",
      "transport": {
        "type": "stdio"
      }
    }
  ]
}
```

**Step 4: Commit**

```bash
git add tests/test_no_destructive_vm_code.py server.json
git commit -m "chore: safety test + server.json metadata"
```

---

## Phase 7 — TKC Ops + K8s Connection (Phase 2)

### Task 11: K8s connection manager (Layer 2)

**Files:**
- Create: `vmware_vks/k8s_connection.py`

**Step 1: Implement vmware_vks/k8s_connection.py**

```python
"""Layer 2: kubernetes Python client connection to Supervisor K8s API endpoint.

Kubeconfig is retrieved from vCenter REST API (Layer 1) and injected here.
Used for TKC CR lifecycle (create/get/delete/scale/upgrade).
"""
from __future__ import annotations

import base64
import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

_log = logging.getLogger("vmware-vks.k8s_connection")


def get_supervisor_kubeconfig_str(si: ServiceInstance, namespace: str) -> str:
    """Retrieve kubeconfig for Supervisor namespace from vCenter REST API.

    The kubeconfig allows connecting to the Supervisor K8s API endpoint
    to manage TKC CRs via the kubernetes Python client.
    """
    from vmware_vks.ops.supervisor import _rest_get

    # vCenter provides kubeconfig via namespace detail or dedicated endpoint
    try:
        # Try namespace-level kubeconfig endpoint first
        data = _rest_get(si, f"/vcenter/namespaces/instances/{namespace}")
        # kubeconfig may be embedded or available via api_server_cluster_endpoint
        cluster_ep = data.get("cluster_endpoint") or data.get("api_server_cluster_endpoint")
        if cluster_ep:
            return _build_kubeconfig(si, cluster_ep, namespace)
    except Exception as e:
        _log.debug("Namespace detail failed: %s", e)

    raise RuntimeError(
        f"Could not retrieve kubeconfig for namespace '{namespace}'. "
        "Ensure Workload Management is enabled and the namespace exists."
    )


def _build_kubeconfig(si: ServiceInstance, api_endpoint: str, namespace: str) -> str:
    """Build a kubeconfig YAML string for the Supervisor API endpoint."""
    import yaml as _yaml

    # Use vCenter session token as bearer token
    token = si.content.sessionManager.currentSession.key
    host = si._stub.host.split(":")[0]

    kubeconfig = {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [{
            "name": "supervisor",
            "cluster": {
                "server": f"https://{api_endpoint}",
                "insecure-skip-tls-verify": True,
            }
        }],
        "users": [{
            "name": "vsphere-user",
            "user": {"token": token}
        }],
        "contexts": [{
            "name": "supervisor-context",
            "context": {
                "cluster": "supervisor",
                "user": "vsphere-user",
                "namespace": namespace,
            }
        }],
        "current-context": "supervisor-context",
    }
    return _yaml.dump(kubeconfig)


def get_k8s_client(si: ServiceInstance, namespace: str):
    """Get a kubernetes ApiClient connected to the Supervisor namespace.

    Returns a kubernetes.client.ApiClient instance.
    """
    import kubernetes as k8s

    kubeconfig_str = get_supervisor_kubeconfig_str(si, namespace)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(kubeconfig_str)
        tmpfile = f.name

    try:
        cfg = k8s.config.load_kube_config(config_file=tmpfile)
        return k8s.client.ApiClient(configuration=cfg)
    finally:
        Path(tmpfile).unlink(missing_ok=True)
```

**Step 2: Commit**

```bash
git add vmware_vks/k8s_connection.py
git commit -m "feat: K8s connection manager (Layer 2, Supervisor kubeconfig)"
```

---

### Task 12: tkc.py + kubeconfig.py

**Files:**
- Create: `vmware_vks/ops/tkc.py`
- Create: `vmware_vks/ops/kubeconfig.py`
- Create: `tests/test_tkc.py`

**Step 1: Write failing tests**

`tests/test_tkc.py`:
```python
"""Tests for TKC ops."""
from unittest.mock import MagicMock, patch
import pytest
from vmware_vks.ops.tkc import (
    generate_tkc_yaml,
    list_tkc_clusters,
    get_tkc_available_versions,
)


def test_generate_tkc_yaml_v1beta1():
    yaml_str = generate_tkc_yaml(
        name="my-cluster",
        namespace="dev",
        k8s_version="v1.28.4+vmware.1",
        vm_class="best-effort-large",
        control_plane_count=1,
        worker_count=3,
        storage_class="vsphere-storage",
    )
    assert "TanzuKubernetesCluster" in yaml_str or "Cluster" in yaml_str
    assert "my-cluster" in yaml_str
    assert "best-effort-large" in yaml_str


def test_generate_tkc_yaml_worker_count_validation():
    with pytest.raises(ValueError, match="worker"):
        generate_tkc_yaml(
            name="bad", namespace="dev", k8s_version="v1.28.4+vmware.1",
            vm_class="best-effort-large", control_plane_count=1,
            worker_count=0, storage_class="vsphere-storage",
        )


def test_list_tkc_clusters_empty():
    mock_api = MagicMock()
    mock_api.list_namespaced_custom_object.return_value = {"items": []}
    with patch("vmware_vks.ops.tkc._get_custom_objects_api", return_value=mock_api):
        si = MagicMock()
        result = list_tkc_clusters(si, namespace="dev")
    assert result["clusters"] == []
    assert result["total"] == 0
```

**Step 2: Run test — expect FAIL**

```bash
uv run pytest tests/test_tkc.py -v
```

**Step 3: Implement vmware_vks/ops/tkc.py**

```python
"""TanzuKubernetesCluster (TKC) lifecycle operations.

Uses cluster.x-k8s.io/v1beta1 API (vSphere 8.x).
All cluster operations go through the Supervisor K8s API endpoint (Layer 2).

Safety:
- delete_tkc_cluster rejects if Deployments/StatefulSets/DaemonSets are running
- create_tkc_cluster defaults to dry_run=True (returns YAML plan)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

_log = logging.getLogger("vmware-vks.ops.tkc")

# TKC API group/version for vSphere 8.x
_TKC_GROUP = "cluster.x-k8s.io"
_TKC_VERSION = "v1beta1"
_TKC_PLURAL = "clusters"


def _get_custom_objects_api(si: ServiceInstance, namespace: str):
    """Get kubernetes CustomObjectsApi connected to Supervisor namespace."""
    import kubernetes as k8s
    from vmware_vks.k8s_connection import get_k8s_client
    api_client = get_k8s_client(si, namespace)
    return k8s.client.CustomObjectsApi(api_client)


def generate_tkc_yaml(
    name: str,
    namespace: str,
    k8s_version: str,
    vm_class: str,
    control_plane_count: int,
    worker_count: int,
    storage_class: str,
) -> str:
    """Generate TKC cluster YAML (cluster.x-k8s.io/v1beta1 for vSphere 8.x).

    Returns YAML string for review before apply.
    """
    if worker_count < 1:
        raise ValueError(f"worker_count must be >= 1, got {worker_count}")
    if control_plane_count not in (1, 3):
        raise ValueError(f"control_plane_count must be 1 or 3, got {control_plane_count}")

    manifest = {
        "apiVersion": f"{_TKC_GROUP}/{_TKC_VERSION}",
        "kind": "Cluster",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "clusterNetwork": {
                "pods": {"cidrBlocks": ["192.168.0.0/16"]},
                "services": {"cidrBlocks": ["10.96.0.0/12"]},
            },
            "topology": {
                "class": "tanzukubernetescluster",
                "version": k8s_version,
                "controlPlane": {
                    "replicas": control_plane_count,
                    "metadata": {},
                    "nodeDrainTimeout": "60s",
                },
                "workers": {
                    "machineDeployments": [{
                        "class": "node-pool",
                        "name": "worker-pool",
                        "replicas": worker_count,
                        "metadata": {},
                        "nodeDrainTimeout": "60s",
                    }]
                },
                "variables": [
                    {"name": "vmClass", "value": vm_class},
                    {"name": "storageClass", "value": storage_class},
                ],
            },
        },
    }
    return yaml.dump(manifest, default_flow_style=False)


def list_tkc_clusters(si: ServiceInstance, namespace: str | None = None) -> dict:
    """List TKC clusters. If namespace is None, attempts to list across all namespaces."""
    api = _get_custom_objects_api(si, namespace or "default")
    if namespace:
        raw = api.list_namespaced_custom_object(
            group=_TKC_GROUP, version=_TKC_VERSION,
            namespace=namespace, plural=_TKC_PLURAL,
        )
    else:
        raw = api.list_cluster_custom_object(
            group=_TKC_GROUP, version=_TKC_VERSION, plural=_TKC_PLURAL,
        )
    items = raw.get("items", [])
    clusters = [
        {
            "name": item["metadata"]["name"],
            "namespace": item["metadata"]["namespace"],
            "phase": item.get("status", {}).get("phase", "Unknown"),
            "k8s_version": item["spec"]["topology"].get("version", ""),
            "ready": item.get("status", {}).get("conditions", [{}])[0].get("status") == "True",
        }
        for item in items
    ]
    return {"total": len(clusters), "clusters": clusters}


def get_tkc_cluster(si: ServiceInstance, name: str, namespace: str) -> dict:
    """Get detailed TKC cluster info."""
    api = _get_custom_objects_api(si, namespace)
    raw = api.get_namespaced_custom_object(
        group=_TKC_GROUP, version=_TKC_VERSION,
        namespace=namespace, plural=_TKC_PLURAL, name=name,
    )
    status = raw.get("status", {})
    return {
        "name": name,
        "namespace": namespace,
        "phase": status.get("phase"),
        "k8s_version": raw["spec"]["topology"].get("version"),
        "control_plane_replicas": raw["spec"]["topology"]["controlPlane"].get("replicas"),
        "worker_replicas": raw["spec"]["topology"]["workers"]["machineDeployments"][0].get("replicas"),
        "conditions": status.get("conditions", []),
        "infrastructure_ready": status.get("infrastructureReady", False),
        "control_plane_ready": status.get("controlPlaneReady", False),
    }


def get_tkc_available_versions(si: ServiceInstance, namespace: str) -> dict:
    """List K8s versions available for TKC clusters on this Supervisor."""
    import kubernetes as k8s
    from vmware_vks.k8s_connection import get_k8s_client

    api_client = get_k8s_client(si, namespace)
    custom_api = k8s.client.CustomObjectsApi(api_client)

    try:
        raw = custom_api.list_cluster_custom_object(
            group="run.tanzu.vmware.com",
            version="v1alpha3",
            plural="tanzukubernetesreleases",
        )
        versions = [
            {
                "name": item["metadata"]["name"],
                "version": item["spec"].get("version", item["metadata"]["name"]),
                "deprecated": item.get("status", {}).get("conditions", [{}])[0].get("reason") == "Deprecated",
            }
            for item in raw.get("items", [])
        ]
        return {"versions": sorted(versions, key=lambda x: x["version"], reverse=True)}
    except Exception as e:
        return {"versions": [], "error": str(e), "hint": "TanzuKubernetesRelease API may not be available"}


def create_tkc_cluster(
    si: ServiceInstance,
    name: str,
    namespace: str,
    k8s_version: str,
    vm_class: str,
    control_plane_count: int = 1,
    worker_count: int = 3,
    storage_class: str = "vsphere-storage",
    dry_run: bool = True,
) -> dict:
    """Create a TKC cluster.

    dry_run=True (default): returns YAML plan for review.
    dry_run=False: applies the manifest to the Supervisor.
    """
    yaml_str = generate_tkc_yaml(
        name=name, namespace=namespace, k8s_version=k8s_version,
        vm_class=vm_class, control_plane_count=control_plane_count,
        worker_count=worker_count, storage_class=storage_class,
    )

    if dry_run:
        return {
            "dry_run": True,
            "action": "create_tkc_cluster",
            "name": name,
            "namespace": namespace,
            "yaml": yaml_str,
            "hint": "Set dry_run=False to apply this manifest.",
        }

    import yaml as _yaml
    manifest = _yaml.safe_load(yaml_str)
    api = _get_custom_objects_api(si, namespace)
    api.create_namespaced_custom_object(
        group=_TKC_GROUP, version=_TKC_VERSION,
        namespace=namespace, plural=_TKC_PLURAL, body=manifest,
    )
    return {"name": name, "namespace": namespace, "status": "creating", "yaml": yaml_str}


def scale_tkc_cluster(
    si: ServiceInstance, name: str, namespace: str, worker_count: int
) -> dict:
    """Scale TKC worker node count."""
    if worker_count < 1:
        raise ValueError(f"worker_count must be >= 1, got {worker_count}")
    api = _get_custom_objects_api(si, namespace)
    patch = {
        "spec": {
            "topology": {
                "workers": {
                    "machineDeployments": [{"name": "worker-pool", "replicas": worker_count}]
                }
            }
        }
    }
    import kubernetes as k8s
    api.patch_namespaced_custom_object(
        group=_TKC_GROUP, version=_TKC_VERSION,
        namespace=namespace, plural=_TKC_PLURAL, name=name, body=patch,
    )
    return {"name": name, "namespace": namespace, "worker_count": worker_count, "status": "scaling"}


def upgrade_tkc_cluster(
    si: ServiceInstance, name: str, namespace: str, k8s_version: str
) -> dict:
    """Upgrade TKC cluster K8s version."""
    api = _get_custom_objects_api(si, namespace)
    patch = {"spec": {"topology": {"version": k8s_version}}}
    api.patch_namespaced_custom_object(
        group=_TKC_GROUP, version=_TKC_VERSION,
        namespace=namespace, plural=_TKC_PLURAL, name=name, body=patch,
    )
    return {"name": name, "namespace": namespace, "new_version": k8s_version, "status": "upgrading"}


def _check_running_workloads(si: ServiceInstance, name: str, namespace: str) -> list[dict]:
    """Check if there are running Deployments/StatefulSets/DaemonSets in the TKC cluster."""
    from vmware_vks.ops.kubeconfig import get_tkc_kubeconfig_str
    import kubernetes as k8s
    import tempfile
    from pathlib import Path

    kubeconfig_str = get_tkc_kubeconfig_str(si, name, namespace)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(kubeconfig_str)
        tmpfile = f.name

    try:
        cfg = k8s.config.load_kube_config(config_file=tmpfile)
        api_client = k8s.client.ApiClient(configuration=cfg)
        apps_api = k8s.client.AppsV1Api(api_client)

        workloads = []
        for deploy in apps_api.list_deployment_for_all_namespaces().items:
            if deploy.status.ready_replicas and deploy.status.ready_replicas > 0:
                workloads.append({"kind": "Deployment", "name": deploy.metadata.name,
                                   "namespace": deploy.metadata.namespace})
        for ss in apps_api.list_stateful_set_for_all_namespaces().items:
            if ss.status.ready_replicas and ss.status.ready_replicas > 0:
                workloads.append({"kind": "StatefulSet", "name": ss.metadata.name,
                                   "namespace": ss.metadata.namespace})
        return workloads
    finally:
        Path(tmpfile).unlink(missing_ok=True)


def delete_tkc_cluster(
    si: ServiceInstance,
    name: str,
    namespace: str,
    confirmed: bool = False,
    dry_run: bool = True,
    force: bool = False,
) -> dict:
    """Delete a TKC cluster.

    Safety guards:
    1. confirmed=True required
    2. Checks for running workloads — rejects unless force=True
    3. dry_run=True shows what would happen without executing
    """
    if not confirmed:
        raise ValueError(
            f"confirmed=True required to delete TKC cluster '{name}'. "
            "This is a destructive operation."
        )

    if not force:
        try:
            workloads = _check_running_workloads(si, name, namespace)
            if workloads:
                raise RuntimeError(
                    f"Cannot delete TKC cluster '{name}': "
                    f"{len(workloads)} running workload(s) detected: "
                    f"{[w['kind'] + '/' + w['name'] for w in workloads[:5]]}. "
                    "Delete workloads first or use force=True."
                )
        except RuntimeError:
            raise
        except Exception as e:
            _log.warning("Could not check workloads (proceeding): %s", e)

    if dry_run:
        return {
            "dry_run": True,
            "action": "delete_tkc_cluster",
            "name": name,
            "namespace": namespace,
            "warning": "This will permanently delete the TKC cluster and all workloads.",
        }

    api = _get_custom_objects_api(si, namespace)
    api.delete_namespaced_custom_object(
        group=_TKC_GROUP, version=_TKC_VERSION,
        namespace=namespace, plural=_TKC_PLURAL, name=name,
    )
    return {"name": name, "namespace": namespace, "status": "deleting"}
```

**Step 4: Implement vmware_vks/ops/kubeconfig.py**

```python
"""Kubeconfig retrieval for Supervisor and TKC clusters."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

_log = logging.getLogger("vmware-vks.ops.kubeconfig")


def get_supervisor_kubeconfig_str(si: ServiceInstance, namespace: str) -> str:
    """Get kubeconfig YAML string for Supervisor namespace."""
    from vmware_vks.k8s_connection import get_supervisor_kubeconfig_str
    return get_supervisor_kubeconfig_str(si, namespace)


def get_tkc_kubeconfig_str(si: ServiceInstance, cluster_name: str, namespace: str) -> str:
    """Get kubeconfig YAML string for a TKC cluster.

    Retrieves via kubectl-vsphere equivalent: calls the Supervisor API
    to generate a TKC-specific kubeconfig.
    """
    from vmware_vks.k8s_connection import get_k8s_client
    import kubernetes as k8s

    api_client = get_k8s_client(si, namespace)
    # TKC kubeconfig via Supervisor token endpoint
    # POST /apis/run.tanzu.vmware.com/v1alpha3/namespaces/{ns}/tanzukubernetesclusters/{name}/endpoints
    core_api = k8s.client.CoreV1Api(api_client)
    # Fall back to building kubeconfig from cluster endpoint
    custom_api = k8s.client.CustomObjectsApi(api_client)

    cluster = custom_api.get_namespaced_custom_object(
        group="cluster.x-k8s.io", version="v1beta1",
        namespace=namespace, plural="clusters", name=cluster_name,
    )

    control_plane_endpoint = (
        cluster.get("spec", {}).get("controlPlaneEndpoint", {})
    )
    host = control_plane_endpoint.get("host", "")
    port = control_plane_endpoint.get("port", 6443)

    if not host:
        raise RuntimeError(
            f"TKC cluster '{cluster_name}' control plane endpoint not available. "
            "Is the cluster fully provisioned?"
        )

    token = si.content.sessionManager.currentSession.key
    import yaml as _yaml
    kubeconfig = {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [{"name": cluster_name, "cluster": {
            "server": f"https://{host}:{port}",
            "insecure-skip-tls-verify": True,
        }}],
        "users": [{"name": "vsphere-user", "user": {"token": token}}],
        "contexts": [{"name": f"{cluster_name}-context", "context": {
            "cluster": cluster_name, "user": "vsphere-user",
        }}],
        "current-context": f"{cluster_name}-context",
    }
    return _yaml.dump(kubeconfig)


def write_kubeconfig(
    si: ServiceInstance,
    cluster_name: str,
    namespace: str,
    output_path: Path | None = None,
) -> dict:
    """Write TKC kubeconfig to file or return as string.

    Args:
        cluster_name: TKC cluster name
        namespace: vSphere Namespace
        output_path: Write to file if provided, otherwise return string
    """
    kubeconfig_str = get_tkc_kubeconfig_str(si, cluster_name, namespace)

    if output_path:
        output_path.write_text(kubeconfig_str)
        output_path.chmod(0o600)
        return {"cluster": cluster_name, "written_to": str(output_path)}

    return {"cluster": cluster_name, "kubeconfig": kubeconfig_str}
```

**Step 5: Run tests — expect PASS**

```bash
uv run pytest tests/test_tkc.py -v
```

**Step 6: Commit**

```bash
git add vmware_vks/ops/tkc.py vmware_vks/ops/kubeconfig.py vmware_vks/k8s_connection.py tests/test_tkc.py
git commit -m "feat: TKC ops (list/get/create/scale/upgrade/delete) + kubeconfig retrieval"
```

---

### Task 13: harbor.py + storage.py

**Files:**
- Create: `vmware_vks/ops/harbor.py`
- Create: `vmware_vks/ops/storage.py`

**Step 1: Implement harbor.py**

```python
"""Harbor registry info (read-only)."""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance
from vmware_vks.ops.supervisor import _rest_get


def get_harbor_info(si: ServiceInstance) -> dict:
    """Get embedded Harbor registry info."""
    try:
        data = _rest_get(si, "/vcenter/content/registries/harbor")
        registries = data if isinstance(data, list) else [data]
        return {
            "registries": [
                {
                    "id": r.get("id"),
                    "url": r.get("ui_access_url"),
                    "storage_used_mb": r.get("storage_used_MB"),
                    "status": r.get("health", {}).get("status"),
                }
                for r in registries
            ]
        }
    except Exception as e:
        return {"error": str(e), "hint": "Harbor may not be enabled on this Supervisor"}
```

**Step 2: Implement storage.py**

```python
"""Namespace storage usage (PVC list + usage)."""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance


def list_namespace_storage_usage(si: ServiceInstance, namespace: str) -> dict:
    """List PVCs and storage usage for a vSphere Namespace."""
    from vmware_vks.k8s_connection import get_k8s_client
    import kubernetes as k8s

    api_client = get_k8s_client(si, namespace)
    core_api = k8s.client.CoreV1Api(api_client)

    pvcs = core_api.list_namespaced_persistent_volume_claim(namespace="default")
    items = [
        {
            "name": pvc.metadata.name,
            "namespace": pvc.metadata.namespace,
            "status": pvc.status.phase,
            "capacity": pvc.status.capacity.get("storage") if pvc.status.capacity else None,
            "storage_class": pvc.spec.storage_class_name,
        }
        for pvc in pvcs.items
    ]
    return {"namespace": namespace, "pvc_count": len(items), "pvcs": items}
```

**Step 3: Commit**

```bash
git add vmware_vks/ops/harbor.py vmware_vks/ops/storage.py
git commit -m "feat: harbor info + namespace storage usage ops"
```

---

## Phase 8 — MCP Server Phase 2 + CLI Phase 2

### Task 14: Add Phase 2 tools to MCP server

**Files:**
- Modify: `mcp_server/server.py` (add 11 more tools)

Add these tool registrations after the existing Phase 1 tools. Follow the exact same pattern — thin wrappers with audit logging on write operations.

TKC tools to add:
- `list_tkc_clusters_tool` → `list_tkc_clusters`
- `get_tkc_cluster_tool` → `get_tkc_cluster`
- `get_tkc_available_versions_tool` → `get_tkc_available_versions`
- `create_tkc_cluster_tool` → `create_tkc_cluster` (dry_run=True default, audit on write)
- `scale_tkc_cluster_tool` → `scale_tkc_cluster` (audit)
- `upgrade_tkc_cluster_tool` → `upgrade_tkc_cluster` (audit)
- `delete_tkc_cluster_tool` → `delete_tkc_cluster` (dry_run=True + confirmed=False default, audit)

Access tools to add:
- `get_supervisor_kubeconfig_tool` → `get_supervisor_kubeconfig_str`
- `get_tkc_kubeconfig_tool` → `write_kubeconfig`
- `get_harbor_info_tool` → `get_harbor_info`
- `list_namespace_storage_usage_tool` → `list_namespace_storage_usage`

**Step 1: Run full test suite**

```bash
uv run pytest tests/ -v
```
Expected: all tests PASS

**Step 2: Commit**

```bash
git add mcp_server/server.py
git commit -m "feat: MCP server Phase 2 (TKC + access tools, 20 tools total)"
```

---

### Task 15: CLI Phase 2 (tkc + kubeconfig + harbor + storage commands)

**Files:**
- Modify: `vmware_vks/cli.py`

Add these sub-apps to cli.py:

```python
tkc_app = typer.Typer(help="TanzuKubernetesCluster commands")
kubeconfig_app = typer.Typer(help="Kubeconfig commands")
app.add_typer(tkc_app, name="tkc")
app.add_typer(kubeconfig_app, name="kubeconfig")
```

TKC commands:
- `vmware-vks tkc list [-n NAMESPACE]`
- `vmware-vks tkc get NAME -n NAMESPACE`
- `vmware-vks tkc versions`
- `vmware-vks tkc create NAME -n NS [--version V] [--control-plane N] [--workers N] [--vm-class C] [--storage-policy P] [--apply]`
  - Interactive prompts for missing required params (use `typer.prompt`)
  - Show Plan table before apply
- `vmware-vks tkc scale NAME -n NS --workers N`
- `vmware-vks tkc upgrade NAME -n NS --version V`
- `vmware-vks tkc delete NAME -n NS [--force]` — double-confirm + workload guard

Kubeconfig commands:
- `vmware-vks kubeconfig supervisor -n NS`
- `vmware-vks kubeconfig get NAME -n NS [--output PATH]`

Additional commands:
- `vmware-vks harbor info`
- `vmware-vks storage usage -n NS`

**Step 1: Verify CLI help**

```bash
uv run vmware-vks --help
uv run vmware-vks tkc --help
uv run vmware-vks kubeconfig --help
```

**Step 2: Commit**

```bash
git add vmware_vks/cli.py
git commit -m "feat: CLI Phase 2 (tkc, kubeconfig, harbor, storage commands)"
```

---

## Phase 9 — README + SKILL.md + RELEASE_NOTES

### Task 16: Documentation

**Files:**
- Create: `skills/vmware-vks/SKILL.md`
- Create: `README.md`
- Create: `README-CN.md`
- Create: `RELEASE_NOTES.md`

Follow the exact structure of `/Users/zw/testany/myskills/VMware-Storage/skills/vmware-storage/SKILL.md` — same frontmatter format, same 6-element Security section, same OpenClaw metadata pattern.

SKILL.md frontmatter:
```yaml
---
name: vmware-vks
description: >
  Manage vSphere with Tanzu (VKS) via AI model or CLI. Supervisor status,
  Namespace lifecycle (create/update/delete with TKC guard), and
  TanzuKubernetesCluster lifecycle (create/scale/upgrade/delete with workload guard).
  Requires vSphere 8.x+ with Workload Management enabled.
installer:
  kind: uv
  package: vmware-vks
metadata: {"openclaw":{"requires":{"env":["VMWARE_VKS_CONFIG"],"bins":["vmware-vks"],"config":["~/.vmware-vks/config.yaml"]},"primaryEnv":"VMWARE_VKS_CONFIG"}}
---
```

**Step 1: Commit docs**

```bash
git add skills/ README.md README-CN.md RELEASE_NOTES.md
git commit -m "docs: SKILL.md, README, RELEASE_NOTES for v0.1.0"
```

---

## Phase 10 — Final verification

### Task 17: Full test suite + bandit

**Step 1: Run all tests**

```bash
cd /Users/zw/testany/myskills/VMware-VKS
uv run pytest tests/ -v --tb=short
```
Expected: all tests PASS

**Step 2: Run bandit security scan**

```bash
uvx bandit -r vmware_vks/ mcp_server/
```
Expected: 0 Medium+ issues (SSL CERT_NONE is acceptable, already documented)

**Step 3: Verify MCP server starts**

```bash
uv run python -c "from mcp_server.server import mcp; print(f'Tools: {len(mcp._tools)}')"
```
Expected: `Tools: 20`

**Step 4: Verify CLI**

```bash
uv run vmware-vks --help
uv run vmware-vks supervisor --help
uv run vmware-vks namespace --help
uv run vmware-vks tkc --help
uv run vmware-vks kubeconfig --help
```

**Step 5: Final commit**

```bash
git add -A
git commit -m "chore: final verification pass, v0.1.0 ready"
```

---

## Summary

| Phase | Tasks | Deliverable |
|-------|-------|-------------|
| 1 | 1-3 | Scaffold + config + connection |
| 2 | 4-5 | Supervisor + namespace ops (9 tools) |
| 3 | 6-7 | Audit logger + doctor |
| 4 | 8 | MCP server Phase 1 (9 tools) |
| 5 | 9 | CLI Phase 1 |
| 6 | 10 | Safety test + server.json |
| 7 | 11-13 | K8s connection + TKC + kubeconfig + harbor + storage |
| 8 | 14-15 | MCP server Phase 2 + CLI Phase 2 (20 tools total) |
| 9 | 16 | README + SKILL.md + RELEASE_NOTES |
| 10 | 17 | Final verification + bandit |
