> Detailed usage and architecture reference. [Quick overview](../../../README.md).


# VMware VKS

[![PyPI](https://img.shields.io/pypi/v/vmware-vks)](https://pypi.org/project/vmware-vks/) [![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](#readme-license)

English | [中文](detailed-guide-CN.md)

Maintainer: Wei Zhou · [zhouwei008@gmail.com](mailto:zhouwei008@gmail.com)

Community-maintained open-source project; not an official VMware or Broadcom product.

[Overview](#readme-overview) · [Quick Start](#readme-quick-start) · [MCP Configuration](#readme-mcp-configuration) · [Capabilities and Workflows](#readme-capabilities-and-workflows) · [Safety and Audit](#readme-safety-and-audit) · [Troubleshooting](#readme-troubleshooting) · [Companion Skills](#readme-companion-skills) · [Reference](#readme-reference) · [License](#readme-license)

<a id="readme-overview"></a>

## Overview

Runs locally over stdio by default; configure required targets and credentials on your own machine.

MCP Skill + CLI for VMware vSphere Kubernetes Service (VKS) management — Supervisor clusters, vSphere Namespaces, and VKS Cluster lifecycle. 25 MCP tools.


MCP uses local stdio by default. Opt-in HTTPS opens the configured listener and requires TLS plus caller authentication; it is a network service, not a local-only mode. Credential examples create owner-only placeholder files without overwriting existing files; edit secrets locally.

Skills.sh examples pin `skills@1.7.6` (Node.js >=22.20.0) separately from the repository release tag. These pins do not lock transitive dependencies.

Full agent instructions: [`skills/vmware-vks/SKILL.md`](../../../skills/vmware-vks/SKILL.md).

<a id="readme-quick-start"></a>

## Quick Start

Requires Python `>=3.10` and `uv`.

```bash
uv tool install vmware-vks==3.7.2
vmware-vks --help
```

Target, credential and local data setup: [setup guide](../../../skills/vmware-vks/references/setup-guide.md).

<details>
<summary>Installation and configuration details (including offline setup)</summary>

### Prerequisites

- **Python 3.10+** — required for `uv tool install`
- **vSphere 8.0+** — Workload Management (Supervisor) APIs require vSphere 8.x
- **Workload Management enabled** — WCP must be enabled on at least one compute cluster
- **License** — vSphere Kubernetes Service (Enterprise Plus or VMware Cloud Foundation)

Run `vmware-vks check` after setup to verify all requirements are met.


### Quick Start

```bash
# Install
uv tool install vmware-vks==3.7.2

# Configure
mkdir -p ~/.vmware-vks
cp config.example.yaml ~/.vmware-vks/config.yaml
# Edit config.yaml with your vCenter host and username

# One password env var per target, named after the target in config.yaml:
# target "vcenter01" (the one config.example.yaml ships) -> VMWARE_VKS_VCENTER01_PASSWORD
(
  umask 077
  set -C  # Refuse existing files; edit an existing .env locally instead
  cat > ~/.vmware-vks/.env <<'EOF'
VMWARE_VKS_VCENTER01_PASSWORD=REPLACE_IN_LOCAL_EDITOR
EOF
)
# Before continuing, edit .env locally; never type live secrets into shell commands.

# Verify
vmware-vks check

# Common operations
vmware-vks supervisor status domain-c1
vmware-vks namespace list
vmware-vks tkc list
vmware-vks tkc create my-cluster -n dev --version v1.28.4+vmware.1 --vm-class best-effort-large
vmware-vks tkc create my-cluster -n dev --apply
```

This writes placeholders only. Enter passwords or tokens using a local editor after creation; do not paste live credentials into terminal commands, chat or commits. If the file exists, the command refuses to overwrite it; edit the existing file after checking its permissions are `600`.


### Offline / Air-Gapped Install (from source)

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


</details>

<a id="readme-mcp-configuration"></a>

## MCP Configuration

Copy this configuration into your MCP client's local stdio settings. The client must be able to find `uvx`; the initial package installation requires access to a package index.

```json
{
  "mcpServers": {
    "vmware-vks": {
      "command": "uvx",
      "args": ["--from", "vmware-vks==3.7.2", "vmware-vks-mcp"],
      "env": {
        "VMWARE_VKS_CONFIG": "~/.vmware-vks/config.yaml"
      }
    }
  }
}
```

The following sections retain preinstalled CLI alternatives and product-specific MCP options.

<details>
<summary>Preinstalled CLI and additional MCP options</summary>

### MCP Server

**After `uv tool install vmware-vks==3.7.2`, start the MCP server with one command** (v1.5.15+):

```bash
# Recommended — single command, no network re-resolve
vmware-vks mcp

# With a custom config path
VMWARE_VKS_CONFIG=/path/to/config.yaml vmware-vks mcp
```

#### Agent Configuration

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
uvx --from vmware-vks==3.7.2 vmware-vks mcp

# Legacy entry point (still works, kept for backward compatibility)
vmware-vks-mcp
```

> **Behind a corporate TLS proxy?** uvx may fail with `invalid peer certificate: UnknownIssuer`.
> Use the recommended `vmware-vks mcp` form above (no network needed), or set `UV_NATIVE_TLS=true`.

</details>


</details>

<a id="readme-capabilities-and-workflows"></a>

## Capabilities and Workflows

### Common Workflows

#### Deploy a New TKC Cluster

1. Check compatibility → `vmware-vks check`
2. List available K8s versions → `vmware-vks tkc versions -n dev`
3. Create namespace (if needed) → `vmware-vks namespace create dev --cluster domain-c1 --storage-policy <policy-id> --cpu 16000 --memory 32768 --apply` (get the policy ID from `vmware-vks supervisor storage-policies`)
4. Create TKC cluster → `vmware-vks tkc create dev-cluster -n dev --version v1.28.4+vmware.1 --control-plane 1 --workers 3 --vm-class best-effort-large --apply`
5. Get kubeconfig (credential; only when you need cluster access) → `vmware-vks kubeconfig get dev-cluster -n dev -o ./kubeconfig` — writes an owner-only file instead of printing the token

#### Scale Workers for Load Testing

1. Check current state → `vmware-vks tkc get dev-cluster -n dev`
2. Scale up → `vmware-vks tkc scale dev-cluster -n dev --workers 6`
3. Monitor progress → `vmware-vks tkc get dev-cluster -n dev` (watch phase)
4. Scale back down after test

#### Namespace Resource Management

1. List namespaces → `vmware-vks namespace list`
2. Check usage → `vmware-vks storage -n dev`
3. Update quota → `vmware-vks namespace update dev --cpu 32000 --memory 65536`

### Tool Reference (25 tools)

#### Supervisor

| Tool | Description | Type |
|------|-------------|------|
| `check_vks_compatibility` | vCenter version check + WCP status | Read |
| `get_supervisor_upgrade_risk` | vCenter 9.1.0/9.1.1 pre-upgrade advisory scope; VUMDB schema remains unmeasured; links to the official checker | Read |
| `get_supervisor_status` | Supervisor cluster status and K8s API endpoint | Read |
| `list_supervisor_storage_policies` | vCenter storage policies (policy ID, name, description) | Read |

For an upgrade from vCenter 8.0 Update 3 or 9.0.0–9.0.2 to 9.1.0/9.1.1, run
`supervisor upgrade-risk` before scheduling. Its report cannot inspect the VUMDB
schema or establish upgrade clearance. If a Supervisor/VKS environment is in
scope, follow the [Broadcom checker and remediation instructions](https://knowledge.broadcom.com/external/article/455737)
or VDT, then back up vCenter and Supervisor before upgrading.

#### Namespace

| Tool | Description | Type |
|------|-------------|------|
| `list_namespaces` | All vSphere Namespaces with status | Read |
| `get_namespace` | Namespace detail (quotas, storage, roles) | Read |
| `create_namespace` | Create Namespace; previews by default (`confirm=True` applies) | Write |
| `update_namespace` | Modify quotas and storage policy | Write |
| `delete_namespace` | Delete with TKC guard (rejects if clusters exist) | Write |
| `list_vm_classes` | Available VM classes for TKC sizing | Read |

#### TKC

| Tool | Description | Type |
|------|-------------|------|
| `list_tkc_clusters` | TanzuKubernetesCluster list with status | Read |
| `get_tkc_cluster` | Cluster detail: every worker pool, known total replicas, health, conditions | Read |
| `get_tkc_available_versions` | Supported K8s versions on Supervisor | Read |
| `create_tkc_cluster` | Create TKC; previews the YAML plan by default (`confirm=True` applies) | Write |
| `scale_tkc_cluster` | Scale worker node count | Write |
| `upgrade_tkc_cluster` | Upgrade K8s version | Write |
| `delete_tkc_cluster` | Delete with workload guard | Write |

`get_tkc_cluster.worker_pools` lists every declared worker pool. `worker_replica_total`
is the sum only when every pool has a known fixed replica count; it is `null`
for an autoscaled or unreadable pool. The older `worker_replicas` field remains
the **first pool only** for compatibility and must not be interpreted as the
cluster total.

#### Access

| Tool | Description | Type |
|------|-------------|------|
| `issue_supervisor_kubeconfig` | Supervisor kubeconfig — credential (bearer token); inline or owner-only file | Write |
| `issue_tkc_kubeconfig` | TKC kubeconfig — credential (bearer token); inline or owner-only file | Write |
| `get_harbor_info` | Embedded Harbor registry info (id, cluster, version, URL, health, storage used) | Read |
| `list_namespace_storage_usage` | PVC list and capacity stats | Read |

**VCF 9.1 TKC access:** In one live 9.1 report, `issue_tkc_kubeconfig` put a Supervisor JWT into the guest kubeconfig and the TKC API rejected it with 401. Treat the generated file as unverified for guest access; test it against the guest API before use. See [issue #27](https://github.com/vmware-skills/VMware-VKS/issues/27). Supervisor-scoped TKC reads are unaffected in that report.

#### VM Service (vm-operator)

| Tool | Description | Type |
|------|-------------|------|
| `list_vm_service_snapshots` | VirtualMachineSnapshot objects in a vSphere Namespace | Read |
| `list_vm_service_groups` | VirtualMachineGroup objects and their boot order in a Namespace | Read |
| `list_vm_service_network_interfaces` | Network interfaces (multi-NIC) of one VM Service VirtualMachine | Read |

#### Permissions

| Tool | Description | Type |
|------|-------------|------|
| `get_access_info` | Which account the server uses and whether it holds each tool's vSphere privileges on the root folder. Kubernetes calls are authorized by the namespace's own permissions, which it does not check | Read |

### CLI Reference

```bash
# Pre-flight diagnostics
vmware-vks check

# Supervisor
vmware-vks supervisor status <cluster-id>
vmware-vks supervisor upgrade-risk --to 9.1.1
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
vmware-vks kubeconfig supervisor -n <namespace> [-o <path>]
vmware-vks kubeconfig get <cluster-name> -n <namespace> [-o <path>]

# Harbor & Storage
vmware-vks harbor
vmware-vks storage -n <namespace>
```

<a id="readme-safety-and-audit"></a>

## Safety and Audit

### Safety

| Feature | Description |
|---------|-------------|
| Read-heavy | 16/25 tools are read-only |
| Preview by default | `create_namespace`, `create_tkc_cluster`, `delete_namespace`, `delete_tkc_cluster` take `confirm` (default `False`): a call without it changes nothing and returns `blast_radius` — what would be created, or the TKC clusters, VMs, PVCs, nodes and workloads a delete destroys. `confirm=True` is refused when a blocker stands or part of the blast radius could not be read. the old `confirmed` / `dry_run` aliases were removed in 3.4.2 and are refused as unknown arguments |
| TKC guard | `delete_namespace` rejects if TKC clusters exist inside |
| Workload guard | `delete_tkc_cluster` rejects if Deployments/StatefulSets are running |
| Credential safety | Passwords only from environment variables (`.env` file), never in `config.yaml` |
| In-memory kubeconfig | For the skill's own API calls the Supervisor/TKC kubeconfig (with the Supervisor bearer token) is built as an in-memory dict and loaded via `load_kube_config_from_dict()` — never written to a temp file on disk (v1.5.18+) |
| Kubeconfig is credential access | `issue_supervisor_kubeconfig` / `issue_tkc_kubeconfig` return a bearer token valid for hours: annotated `readOnlyHint: false` (clients ask first), `risk_level: medium`, audit row redacts the kubeconfig. Run only on explicit request; export with `output_path` / `-o` to an owner-only (0600) file |
| Audit logging | All write operations logged to `~/.vmware-vks/audit.log` |
| MCP transport | Local stdio by default; optional authenticated HTTPS opens the configured listener |

### Remote access (HTTPS) and limiting tools

By default `vmware-vks mcp` speaks stdio to one local client and offers every tool. Two options change that
(vmware-policy ≥ 1.21.0).

**Serve over HTTPS** so clients on other machines can connect. Every caller needs a bearer token:

```bash
vmware-audit token create alice          # prints the token once; give it to alice
vmware-audit token list                  # names only
vmware-audit token revoke alice          # refused from its next request, no restart
vmware-vks mcp --transport https --host 0.0.0.0 --port 8443 \
    --cert /etc/vmware-mcp/server.pem --key /etc/vmware-mcp/server-key.pem \
    --public-name mcp.example.com
```

Connect a client (Claude Code shown; any MCP client that supports streamable HTTP and custom headers works):

```bash
claude mcp add --transport http vmware-vks https://mcp.example.com:8443/mcp \
    --header "Authorization: Bearer $VMWARE_MCP_TOKEN"
```

```json
{
  "mcpServers": {
    "vmware-vks": {
      "type": "http",
      "url": "https://mcp.example.com:8443/mcp",
      "headers": { "Authorization": "Bearer ${VMWARE_MCP_TOKEN}" }
    }
  }
}
```

- The server will not start without a token, refuses Host/Origin values outside `--public-name`, and checks
  before it binds that unauthenticated requests get 401. Use a certificate your clients trust for those names.
- Behind a TLS-terminating reverse proxy on the same machine use `--transport http --host 127.0.0.1
  --public-name mcp.example.com` and pass the `Authorization` header through.
- Behind a load balancer or gateway on another machine, keep `--transport https` (a self-signed certificate is
  fine), list in `--public-name` the host name the balancer sends as `Host`, and have it forward `Authorization`.
  Its health probe can use `GET /healthz`, which needs no token and returns only `{"status": "ok"}`
  (vmware-policy ≥ 1.23.0).
- Send `traceparent` or `X-Request-ID` with each request to have it stored on the audit row;
  `vmware-audit log --correlation-id ID` then finds the calls behind one platform request.
- Tools that read or write files on this server (here: `issue_*_kubeconfig(output_path)`) never touch its configuration, tokens, audit log or
  TLS key, and over the network they are off unless `VMWARE_MCP_FILE_ROOT` names a directory for them
  (vmware-policy ≥ 1.23.1).
- The audit log records each caller as `token:<name>`. Calls run side by side, also on the same target: the server keeps up to `VMWARE_MCP_POOL_SIZE` (default 2) sessions per target, each one a login on that target, and a call waits at most 300 s for a free session (vmware-policy ≥ 1.22.0).
- **Everyone who can reach the endpoint acts with this server's account.** Tokens identify callers; they grant
  nothing on the platform. Run one endpoint per privilege level. `vmware-vks access-info` shows which account that is and what it may do.
- **Several people or roles on one endpoint:** run `vmware-audit roles enable` once, then give each token a role (`vmware-audit token create NAME --role reader`, or a role in `roles.yaml` naming tools and targets; vmware-policy ≥ 1.24.0). A role limits what the token may call on this endpoint; it cannot grant more than the account has. See vmware-policy's README, "Roles for network callers".

**Offer only read tools, plus the writes you choose:**

```bash
vmware-vks mcp --expose read --allow-write create_namespace
# or: VMWARE_VKS_MCP_EXPOSE=read VMWARE_VKS_MCP_ALLOW_WRITE=create_namespace vmware-vks mcp
```

Tools not offered do not appear in the client's tool list. A bad value or an unknown write tool stops the server
(exit 2). This limits what clients are offered; it is not authorization, so the account itself should be
least-privilege.

Details: `skills/vmware-vks/references/setup-guide.md` ("Serving MCP over HTTPS", "Limiting which tools are offered").

<a id="readme-troubleshooting"></a>

## Troubleshooting

### Troubleshooting

#### "VKS not compatible" error

Workload Management must be enabled in vCenter. Check: vCenter UI -> Workload Management. Requires vSphere 8.x+ with Enterprise Plus or VCF license.

#### Every command fails with "session is not authenticated"

The password is right. Either the session expired (run the command again; a new session is opened) or the account has no vSphere role, so vCenter shows no session for it and every call fails. VKS does not refuse such a login when it connects, because an account holding only Supervisor namespace permissions can look the same and still work; the error appears on the first call that needs a role. Assign the account a role (Read-Only or higher) on the vCenter root folder under Administration > Access Control > Global Permissions; Namespace and TKC work also needs Workload Management privileges.

#### Every REST tool returns 401

The namespace, storage-policy and Supervisor-status tools authenticate against
the vSphere Automation REST API with a session id from `POST /api/session` —
**not** the pyVmomi SOAP session key, which that API never issued and always
rejects. A 401 is refreshed automatically once; if it persists, check whether a
proxy between you and vCenter strips the `vmware-api-session-id` header. An
account short of Workload Management permissions gets a **403**, not a 401.

#### Namespace creation fails with "storage policy not found"

List policies first: `vmware-vks supervisor storage-policies`, then pass the **Policy ID** column value (not the display name) as `--storage-policy`.

#### TKC cluster stuck in "Creating" phase

Check Supervisor events in vCenter. Common causes: insufficient resources on ESXi hosts, network issues with NSX-T, or storage policy not available on target datastore.

#### Kubeconfig retrieval fails

Supervisor API endpoint must be reachable from the machine running vmware-vks. Check firewall rules for port 6443.

#### Scale operation has no effect

Verify the cluster is in "Running" phase before scaling. Clusters in "Creating" or "Updating" phase reject scale operations.

#### Delete namespace rejected unexpectedly

The namespace delete guard prevents deletion when TKC clusters exist inside. Delete all TKC clusters in the namespace first, then retry.

<a id="readme-companion-skills"></a>

## Companion Skills

The VMware Skills family contains 14 MCP servers and the shared Policy library. Choose tools by responsibility: [vmware-skills](https://github.com/vmware-skills).

### Companion Skills

> **Part of the VMware MCP Skills family.** Each skill handles a distinct domain — install only what you need.

| Skill | Scope | Tools | Install |
|-------|-------|:-----:|---------|
| **[vmware-aiops](https://github.com/vmware-skills/VMware-AIops)** ⭐ entry point | VM lifecycle, deployment, guest ops, clusters | 49 | `uv tool install vmware-aiops` |
| **[vmware-monitor](https://github.com/vmware-skills/VMware-Monitor)** | Read-only monitoring, alarms, events, VM info | 27 | `uv tool install vmware-monitor` |
| **[vmware-storage](https://github.com/vmware-skills/VMware-Storage)** | Datastores, iSCSI, vSAN | 11 | `uv tool install vmware-storage` |
| **[vmware-nsx](https://github.com/vmware-skills/VMware-NSX)** | NSX networking: segments, gateways, NAT, IPAM | 33 | `uv tool install vmware-nsx-mgmt` |
| **[vmware-nsx-security](https://github.com/vmware-skills/VMware-NSX-Security)** | DFW microsegmentation, security groups, Traceflow | 21 | `uv tool install vmware-nsx-security` |
| **[vmware-aria](https://github.com/vmware-skills/VMware-Aria)** | Aria Ops metrics, alerts, capacity planning | 28 | `uv tool install vmware-aria` |

### Related Projects

| Skill | Scope | Tools | Install |
|-------|-------|:-----:|---------|
| **[vmware-aiops](https://github.com/vmware-skills/VMware-AIops)** ⭐ entry point | VM lifecycle, deployment, guest ops, clusters | 49 | `uv tool install vmware-aiops` |
| **[vmware-monitor](https://github.com/vmware-skills/VMware-Monitor)** | Read-only monitoring, alarms, events, VM info | 27 | `uv tool install vmware-monitor` |
| **[vmware-storage](https://github.com/vmware-skills/VMware-Storage)** | Datastores, iSCSI, vSAN | 11 | `uv tool install vmware-storage` |
| **[vmware-nsx](https://github.com/vmware-skills/VMware-NSX)** | NSX networking: segments, gateways, NAT, IPAM | 33 | `uv tool install vmware-nsx-mgmt` |
| **[vmware-nsx-security](https://github.com/vmware-skills/VMware-NSX-Security)** | DFW microsegmentation, security groups, Traceflow | 21 | `uv tool install vmware-nsx-security` |
| **[vmware-aria](https://github.com/vmware-skills/VMware-Aria)** | Aria Ops metrics, alerts, capacity planning | 28 | `uv tool install vmware-aria` |

<!-- usage: remote-and-exposure -->

<a id="readme-reference"></a>

## Reference

Release history: [RELEASE_NOTES.md](../../../RELEASE_NOTES.md).

Read-only self-test with your own model and agent host: [model-fit self-test](https://github.com/vmware-skills/marketplace/blob/main/MODEL_FIT_SELF_TEST.md).

### Upgrading to 3.0

3.0 renames the two kubeconfig tools: `issue_supervisor_kubeconfig` → `issue_supervisor_kubeconfig`,
`issue_tkc_kubeconfig` → `issue_tkc_kubeconfig`. They issue a credential (a Supervisor bearer token) rather than
read something, and a `get_` name told agents otherwise. Parameters, annotations, safety and CLI commands
(`vmware-vks kubeconfig supervisor` / `kubeconfig get`) are unchanged; callers of the old names must switch (saved vmware-pilot workflows are translated by Pilot ≥ 2.0.0). Deny
rules in `~/.vmware/rules.yaml` naming the old names keep working with vmware-policy ≥ 1.19.0. Details in
[RELEASE_NOTES.md](../../../RELEASE_NOTES.md#v300--kubeconfig-tools-are-named-for-what-they-do).

### Upgrading to 2.0

2.0 renames the three VM Service tools: `list_vm_snapshots` → `list_vm_service_snapshots`, `list_vm_service_groups` →
`list_vm_service_groups`, `list_vm_service_network_interfaces` → `list_vm_service_network_interfaces`. The reason is a name
clash: in the family's 2.0, vmware-aiops and vmware-monitor call their **vSphere** VM snapshot tool `list_vm_snapshots`,
while these list **Kubernetes VM Service** (vm-operator) objects. **Trap:** an agent prompt, script or saved workflow
that still calls `list_vm_snapshots` with vmware-aiops or vmware-monitor also loaded now reaches the vSphere tool, not
this one — update it. A VKS-scoped deny rule naming `list_vm_snapshots` now also matches the aiops/monitor tool
(vmware-policy ≥ 1.18 matches both names; `vmware-audit policy` lists such rules). Parameters, safety gates and CLI
commands are unchanged; the full table is in [RELEASE_NOTES.md](../../../RELEASE_NOTES.md#v200--mcp-tools-renamed-verb-first).
If an install older than 2.0 shares that `rules.yaml`, give it vmware-policy ≥ 1.18.2 too: it matches each rule
against both the old and the new name, so one file serves both.

### Architecture

```
User (Natural Language)
  ↓
AI Agent (Claude Code / Goose / Cursor)
  ↓ reads SKILL.md
  ↓
vmware-vks CLI  ─── or ───  vmware-vks MCP Server (stdio)
  │
  ├─ Layer 1: pyVmomi (/sdk) + vSphere Automation REST (/api)
  │   Supervisor status, storage policies, Namespace CRUD, VM classes, Harbor
  │   Two session stores: the SOAP session from SmartConnect, and a separate
  │     REST session id from POST /api/session (vmware-api-session-id header)
  │
  └─ Layer 2: kubernetes client → Supervisor K8s API endpoint
      TKC CR apply / get / delete (requires a currently served
        cluster.x-k8s.io/v1beta1 or v1beta2 schema; discovery failure stops the operation)
      Kubeconfig built in-memory from the POST /wcp/login bearer token
        (a third credential, and no temp file on disk)
  ↓
vCenter Server 8.x+ (Workload Management enabled)
  ↓
Supervisor Cluster → vSphere Namespaces → TanzuKubernetesCluster
```

### Version Compatibility

| vSphere / VCF | Support | Notes |
|---------|---------|-------|
| 9.0 / 9.1 | ⚠ Partial external 9.1 report | A 9.1 lab confirmed Supervisor `/wcp/login` on the Supervisor endpoint, but its guest TKC API rejects the kubeconfig from this skill with 401 ([#27](https://github.com/vmware-skills/VMware-VKS/issues/27)). Maintainers have not verified the full Workload Management API surface; run `check_vks_compatibility` and report results. |
| 8.0+ | Full | Workload Management APIs available |
| 7.x | Not supported | WCP API surface is different; use vSphere 8.x |

VKS 3.8 introduces vendor-managed chained upgrades and more granular node
placement ([official announcement](https://blogs.vmware.com/cloud-foundation/2026/11/02/vks-3-8-advancing-ai-modern-workloads-private-cloud/)).
This skill's `upgrade_tkc_cluster` still submits one requested Kubernetes version;
it does not verify or orchestrate the 3.8 upgrade chain or placement policy.
The new worker-pool summary is a read of the existing Cluster topology, not a
VCF 9.1/VKS 3.8 compatibility claim. Live 9.1/3.8 validation is pending.

##### Official Broadcom References

- **SDKs**: <https://developer.broadcom.com/sdks> — VCF Python SDK (unified SDK in VCF 9+)
- **REST APIs**: <https://developer.broadcom.com/xapis> — vSphere Automation API (Workload Management endpoints)
- **CLI Tools**: <https://developer.broadcom.com/tools> — kubectl-vsphere, PowerCLI 9.1

<a id="readme-license"></a>

On vCenter 9.1+, optional Supervisor version reads resolve the Supervisor through summaries/topology and use the supported upgrades GET. They require Namespaces.Manage and Namespaces.Upgrade; denied or ambiguous evidence stays unknown. Discovery is bounded to 20 Supervisors. Base health reads still use the existing cluster endpoint; this is not complete retirement of all deprecated cluster APIs or positive 9.1 validation.

## License

[MIT](../../../LICENSE)

**Available from skill 3.7.0; the earlier 3.6.1 package supports v1beta1 only.**
Cluster API creation supports explicit `cluster_class`/`cluster_class_namespace` across
served v1beta1 and v1beta2 schemas. MCP preview and apply require Supervisor admission
dry-run; offline CLI templates remain unvalidated. VCF 9.1 end-to-end deployment is
not lab verified. See [schema boundaries](../../../skills/vmware-vks/references/capabilities.md#measured-cluster-api-schema-support).

MCP safety annotations are client hints, not enforced prompts or proof of
independent human approval. Use the tool's actual preview/confirm contract and
existing Policy/RBAC controls. Read-only inventory may still be sensitive.
