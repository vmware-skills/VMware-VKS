<!-- mcp-name: io.github.vmware-skills/vmware-vks -->
# VMware VKS

> **Author**: Wei Zhou, VMware by Broadcom — wei-wz.zhou@broadcom.com
> This is a community-driven project by a VMware engineer, not an official VMware product.
> For official VMware developer tools see [developer.broadcom.com](https://developer.broadcom.com).

[English](README.md) | [中文](README-CN.md)

MCP Skill + CLI for VMware vSphere Kubernetes Service (VKS) management — Supervisor clusters, vSphere Namespaces, and VKS Cluster lifecycle. 23 MCP tools.

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

## Companion Skills

> **Part of the VMware MCP Skills family.** Each skill handles a distinct domain — install only what you need.

| Skill | Scope | Tools | Install |
|-------|-------|:-----:|---------|
| **[vmware-aiops](https://github.com/vmware-skills/VMware-AIops)** ⭐ entry point | VM lifecycle, deployment, guest ops, clusters | 49 | `uv tool install vmware-aiops` |
| **[vmware-monitor](https://github.com/vmware-skills/VMware-Monitor)** | Read-only monitoring, alarms, events, VM info | 27 | `uv tool install vmware-monitor` |
| **[vmware-storage](https://github.com/vmware-skills/VMware-Storage)** | Datastores, iSCSI, vSAN | 11 | `uv tool install vmware-storage` |
| **[vmware-nsx](https://github.com/vmware-skills/VMware-NSX)** | NSX networking: segments, gateways, NAT, IPAM | 33 | `uv tool install vmware-nsx-mgmt` |
| **[vmware-nsx-security](https://github.com/vmware-skills/VMware-NSX-Security)** | DFW microsegmentation, security groups, Traceflow | 21 | `uv tool install vmware-nsx-security` |
| **[vmware-aria](https://github.com/vmware-skills/VMware-Aria)** | Aria Ops metrics, alerts, capacity planning | 28 | `uv tool install vmware-aria` |

## Prerequisites

- **Python 3.10+** — required for `uv tool install`
- **vSphere 8.0+** — Workload Management (Supervisor) APIs require vSphere 8.x
- **Workload Management enabled** — WCP must be enabled on at least one compute cluster
- **License** — vSphere Kubernetes Service (Enterprise Plus or VMware Cloud Foundation)

Run `vmware-vks check` after setup to verify all requirements are met.

## Quick Start

```bash
# Install
uv tool install vmware-vks

# Configure
mkdir -p ~/.vmware-vks
cp config.example.yaml ~/.vmware-vks/config.yaml
# Edit config.yaml with your vCenter host and username

# One password env var per target, named after the target in config.yaml:
# target "vcenter01" (the one config.example.yaml ships) -> VMWARE_VKS_VCENTER01_PASSWORD
echo "VMWARE_VKS_VCENTER01_PASSWORD=your_password" > ~/.vmware-vks/.env
chmod 600 ~/.vmware-vks/.env

# Verify
vmware-vks check

# Common operations
vmware-vks supervisor status domain-c1
vmware-vks namespace list
vmware-vks tkc list
vmware-vks tkc create my-cluster -n dev --version v1.28.4+vmware.1 --vm-class best-effort-large
vmware-vks tkc create my-cluster -n dev --apply
```

## Offline / Air-Gapped Install (from source)

This project uses the modern PEP 517 build system (hatchling), so there is **no
`setup.py`** by design — that is expected, not a missing file. If you cloned the
source and hit `ERROR: File "setup.py" or "setup.cfg" not found ... editable mode
currently requires a setuptools-based build`, your `pip` is older than 21.3 and
cannot do an *editable* (`-e`) install with a non-setuptools backend. Editable
mode is a developer convenience, not needed to run the tool — do one of:

```bash
# From the source tree — a normal (non-editable) install builds a wheel:
pip install .              # NOT  pip install -e .

# ...or upgrade pip first, and editable works too:
pip install --upgrade pip && pip install -e .
```

For a **truly air-gapped host**, build the wheels on a connected machine and copy
them over — the target then needs no network:

```bash
# On a connected machine, collect this package + its dependencies as wheels:
pip wheel . -w dist        # → dist/*.whl   (or: uv build, for just this package)

# Copy dist/ to the air-gapped host, then install offline:
pip install --no-index --find-links dist vmware-vks
```

## Common Workflows

### Deploy a New TKC Cluster

1. Check compatibility → `vmware-vks check`
2. List available K8s versions → `vmware-vks tkc versions -n dev`
3. Create namespace (if needed) → `vmware-vks namespace create dev --cluster domain-c1 --storage-policy <policy-id> --cpu 16000 --memory 32768 --apply` (get the policy ID from `vmware-vks supervisor storage-policies`)
4. Create TKC cluster → `vmware-vks tkc create dev-cluster -n dev --version v1.28.4+vmware.1 --control-plane 1 --workers 3 --vm-class best-effort-large --apply`
5. Get kubeconfig → `vmware-vks kubeconfig get dev-cluster -n dev`

### Scale Workers for Load Testing

1. Check current state → `vmware-vks tkc get dev-cluster -n dev`
2. Scale up → `vmware-vks tkc scale dev-cluster -n dev --workers 6`
3. Monitor progress → `vmware-vks tkc get dev-cluster -n dev` (watch phase)
4. Scale back down after test

### Namespace Resource Management

1. List namespaces → `vmware-vks namespace list`
2. Check usage → `vmware-vks storage -n dev`
3. Update quota → `vmware-vks namespace update dev --cpu 32000 --memory 65536`

## Tool Reference (20 tools)

### Supervisor

| Tool | Description | Type |
|------|-------------|------|
| `check_vks_compatibility` | vCenter version check + WCP status | Read |
| `get_supervisor_status` | Supervisor cluster status and K8s API endpoint | Read |
| `list_supervisor_storage_policies` | vCenter storage policies (policy ID, name, description) | Read |

### Namespace

| Tool | Description | Type |
|------|-------------|------|
| `list_namespaces` | All vSphere Namespaces with status | Read |
| `get_namespace` | Namespace detail (quotas, storage, roles) | Read |
| `create_namespace` | Create Namespace with dry-run preview | Write |
| `update_namespace` | Modify quotas and storage policy | Write |
| `delete_namespace` | Delete with TKC guard (rejects if clusters exist) | Write |
| `list_vm_classes` | Available VM classes for TKC sizing | Read |

### TKC

| Tool | Description | Type |
|------|-------------|------|
| `list_tkc_clusters` | TanzuKubernetesCluster list with status | Read |
| `get_tkc_cluster` | Cluster detail (nodes, health, conditions) | Read |
| `get_tkc_available_versions` | Supported K8s versions on Supervisor | Read |
| `create_tkc_cluster` | Create TKC with YAML plan + dry-run default | Write |
| `scale_tkc_cluster` | Scale worker node count | Write |
| `upgrade_tkc_cluster` | Upgrade K8s version | Write |
| `delete_tkc_cluster` | Delete with workload guard | Write |

### Access

| Tool | Description | Type |
|------|-------------|------|
| `get_supervisor_kubeconfig` | Supervisor kubeconfig YAML | Read |
| `get_tkc_kubeconfig` | TKC kubeconfig (stdout or file) | Read |
| `get_harbor_info` | Embedded Harbor registry info (id, cluster, version, URL, health, storage used) | Read |
| `list_namespace_storage_usage` | PVC list and capacity stats | Read |

## Architecture

```
User (Natural Language)
  ↓
AI Agent (Claude Code / Goose / Cursor)
  ↓ reads SKILL.md
  ↓
vmware-vks CLI  ─── or ───  vmware-vks MCP Server (stdio)
  │
  ├─ Layer 1: pyVmomi → vCenter REST API
  │   Supervisor status, storage policies, Namespace CRUD, VM classes, Harbor
  │
  └─ Layer 2: kubernetes client → Supervisor K8s API endpoint
      TKC CR apply / get / delete  (cluster.x-k8s.io API version auto-detected:
        prefers v1 when Supervisor serves it, falls back to v1beta1 for vSphere 8.0)
      Kubeconfig built in-memory from Layer 1 session token (no temp file on disk)
  ↓
vCenter Server 8.x+ (Workload Management enabled)
  ↓
Supervisor Cluster → vSphere Namespaces → TanzuKubernetesCluster
```

## CLI Reference

```bash
# Pre-flight diagnostics
vmware-vks check

# Supervisor
vmware-vks supervisor status <cluster-id>
vmware-vks supervisor storage-policies

# Namespace
vmware-vks namespace list
vmware-vks namespace get <name>
vmware-vks namespace create <name> --cluster <id> --storage-policy <policy>
vmware-vks namespace create <name> --cluster <id> --storage-policy <policy> --apply
vmware-vks namespace update <name> [--cpu <mhz>] [--memory <mib>]
vmware-vks namespace delete <name>
vmware-vks namespace vm-classes

# VKS Cluster
vmware-vks tkc list [-n <namespace>]
vmware-vks tkc get <name> -n <namespace>
vmware-vks tkc versions -n <namespace>
vmware-vks tkc create <name> -n <namespace> [--version <v>] [--vm-class <c>]
vmware-vks tkc create <name> -n <namespace> --apply
vmware-vks tkc scale <name> -n <namespace> --workers <n>
vmware-vks tkc upgrade <name> -n <namespace> --version <v>
vmware-vks tkc delete <name> -n <namespace>

# Kubeconfig
vmware-vks kubeconfig supervisor -n <namespace>
vmware-vks kubeconfig get <cluster-name> -n <namespace> [-o <path>]

# Harbor & Storage
vmware-vks harbor
vmware-vks storage -n <namespace>
```

## MCP Server

**After `uv tool install vmware-vks`, start the MCP server with one command** (v1.5.15+):

```bash
# Recommended — single command, no network re-resolve
vmware-vks mcp

# With a custom config path
VMWARE_VKS_CONFIG=/path/to/config.yaml vmware-vks mcp
```

### Agent Configuration

Add to your AI agent's MCP config:

```json
{
  "mcpServers": {
    "vmware-vks": {
      "command": "vmware-vks",
      "args": ["mcp"],
      "env": {
        "VMWARE_VKS_CONFIG": "~/.vmware-vks/config.yaml"
      }
    }
  }
}
```

<details>
<summary>Alternative: uvx (no install) or legacy entry point</summary>

```bash
# Run without installing (requires PyPI access each launch)
uvx --from vmware-vks vmware-vks mcp

# Legacy entry point (still works, kept for backward compatibility)
vmware-vks-mcp
```

> **Behind a corporate TLS proxy?** uvx may fail with `invalid peer certificate: UnknownIssuer`.
> Use the recommended `vmware-vks mcp` form above (no network needed), or set `UV_NATIVE_TLS=true`.

</details>

## Safety

| Feature | Description |
|---------|-------------|
| Read-heavy | 16/23 tools are read-only |
| Dry-run default | `create_namespace`, `create_tkc_cluster`, `delete_namespace`, `delete_tkc_cluster` all default to `dry_run=True` |
| TKC guard | `delete_namespace` rejects if TKC clusters exist inside |
| Workload guard | `delete_tkc_cluster` rejects if Deployments/StatefulSets are running |
| Credential safety | Passwords only from environment variables (`.env` file), never in `config.yaml` |
| In-memory kubeconfig | Supervisor/TKC kubeconfig (with vCenter session bearer token) is built as an in-memory dict and loaded via `load_kube_config_from_dict()` — never written to a temp file on disk (v1.5.18+) |
| Audit logging | All write operations logged to `~/.vmware-vks/audit.log` |
| stdio transport | No network listener; MCP runs over stdio only |

## Troubleshooting

### "VKS not compatible" error

Workload Management must be enabled in vCenter. Check: vCenter UI -> Workload Management. Requires vSphere 8.x+ with Enterprise Plus or VCF license.

### Namespace creation fails with "storage policy not found"

List policies first: `vmware-vks supervisor storage-policies`, then pass the **Policy ID** column value (not the display name) as `--storage-policy`.

### TKC cluster stuck in "Creating" phase

Check Supervisor events in vCenter. Common causes: insufficient resources on ESXi hosts, network issues with NSX-T, or storage policy not available on target datastore.

### Kubeconfig retrieval fails

Supervisor API endpoint must be reachable from the machine running vmware-vks. Check firewall rules for port 6443.

### Scale operation has no effect

Verify the cluster is in "Running" phase before scaling. Clusters in "Creating" or "Updating" phase reject scale operations.

### Delete namespace rejected unexpectedly

The namespace delete guard prevents deletion when TKC clusters exist inside. Delete all TKC clusters in the namespace first, then retry.

## Version Compatibility

| vSphere / VCF | Support | Notes |
|---------|---------|-------|
| 9.0 / 9.1 | ⚠ Not yet verified | Workload Management (Supervisor / WCP) API surface in vSphere 9 has not been tested by maintainers. Existing vSphere 8.x code paths should work but no guarantees until a lab run is completed — basic CRUD likely works, corner cases may need testing. File issues with `check_vks_compatibility` output if you run this on VCF 9. |
| 8.0+ | Full | Workload Management APIs available |
| 7.x | Not supported | WCP API surface is different; use vSphere 8.x |

#### Official Broadcom References

- **SDKs**: <https://developer.broadcom.com/sdks> — VCF Python SDK (unified SDK in VCF 9+)
- **REST APIs**: <https://developer.broadcom.com/xapis> — vSphere Automation API (Workload Management endpoints)
- **CLI Tools**: <https://developer.broadcom.com/tools> — kubectl-vsphere, PowerCLI 9.1

## Related Projects

| Skill | Scope | Tools | Install |
|-------|-------|:-----:|---------|
| **[vmware-aiops](https://github.com/vmware-skills/VMware-AIops)** ⭐ entry point | VM lifecycle, deployment, guest ops, clusters | 49 | `uv tool install vmware-aiops` |
| **[vmware-monitor](https://github.com/vmware-skills/VMware-Monitor)** | Read-only monitoring, alarms, events, VM info | 27 | `uv tool install vmware-monitor` |
| **[vmware-storage](https://github.com/vmware-skills/VMware-Storage)** | Datastores, iSCSI, vSAN | 11 | `uv tool install vmware-storage` |
| **[vmware-nsx](https://github.com/vmware-skills/VMware-NSX)** | NSX networking: segments, gateways, NAT, IPAM | 33 | `uv tool install vmware-nsx-mgmt` |
| **[vmware-nsx-security](https://github.com/vmware-skills/VMware-NSX-Security)** | DFW microsegmentation, security groups, Traceflow | 21 | `uv tool install vmware-nsx-security` |
| **[vmware-aria](https://github.com/vmware-skills/VMware-Aria)** | Aria Ops metrics, alerts, capacity planning | 28 | `uv tool install vmware-aria` |

## License

[MIT](LICENSE)
