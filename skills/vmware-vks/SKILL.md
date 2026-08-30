---
name: vmware-vks
description: >
  Use this skill whenever the user needs to manage vSphere Kubernetes Service (VKS) — Supervisor clusters, vSphere Namespaces, and TKC cluster lifecycle.
  Directly handles: check VKS compatibility, create/delete namespaces, create/scale/upgrade/delete TKC clusters, get kubeconfig, check Harbor registry.
  Always use this skill for "create Kubernetes cluster", "scale workers", "upgrade K8s version", "create namespace", "get kubeconfig", or any VKS/TKC task.
  Do NOT use for vanilla VM operations (use vmware-aiops), non-vSphere Kubernetes (e.g., kubeadm, EKS, AKS), or AVI/AKO load balancing (use vmware-avi).
  For networking use vmware-nsx.
installer:
  kind: uv
  package: vmware-vks
allowed-tools:
  - Bash
metadata: {"openclaw":{"requires":{"env":["VMWARE_VKS_CONFIG"],"bins":["vmware-vks"],"config":["~/.vmware-vks/config.yaml","~/.vmware-vks/.env"]},"optional":{"env":["VMWARE_VKS_<TARGET>_PASSWORD","VMWARE_VKS_<TARGET>_USERNAME","VMWARE_AUDIT_APPROVED_BY"],"bins":["vmware-policy"]},"primaryEnv":"VMWARE_VKS_CONFIG","homepage":"https://github.com/vmware-skills/VMware-VKS","emoji":"☸️","os":["macos","linux"]}}
compatibility: >
  vmware-policy auto-installed as Python dependency (provides @vmware_tool decorator and audit logging). All write operations audited to ~/.vmware/audit.db (SQLite, via vmware-policy) with a local JSON-Lines mirror at ~/.vmware-vks/audit.log.
  Credentials: Each vCenter target requires a per-target password env var in ~/.vmware-vks/.env following the pattern VMWARE_VKS_<TARGET_NAME_UPPER>_PASSWORD (e.g., target "vcenter-01" → VMWARE_VKS_VCENTER_01_PASSWORD). Passwords are never logged, never echoed, never included in audit entries. Kubeconfig tokens returned by get_supervisor_kubeconfig and get_tkc_kubeconfig are short-lived vCenter session tokens, not persistent credentials.
---

# VMware VKS

> **Disclaimer**: This is a community-maintained open-source project and is **not affiliated with, endorsed by, or sponsored by VMware, Inc. or Broadcom Inc.** "VMware" and "vSphere" are trademarks of Broadcom. Source code is publicly auditable at [github.com/vmware-skills/VMware-VKS](https://github.com/vmware-skills/VMware-VKS) under the MIT license.

AI-powered VMware vSphere Kubernetes Service (VKS) management — 23 MCP tools.

> Requires vSphere 8.x+ with Workload Management enabled.
> **Companion skills**: [vmware-aiops](https://github.com/vmware-skills/VMware-AIops) (VM lifecycle), [vmware-monitor](https://github.com/vmware-skills/VMware-Monitor) (monitoring), [vmware-storage](https://github.com/vmware-skills/VMware-Storage) (storage), [vmware-nsx](https://github.com/vmware-skills/VMware-NSX) (NSX networking), [vmware-nsx-security](https://github.com/vmware-skills/VMware-NSX-Security) (DFW/firewall), [vmware-aria](https://github.com/vmware-skills/VMware-Aria) (metrics/alerts/capacity), [vmware-avi](https://github.com/vmware-skills/VMware-AVI) (AVI/ALB/AKO), [vmware-harden](https://github.com/vmware-skills/VMware-Harden) (compliance baselines).
> | [vmware-pilot](../vmware-pilot/SKILL.md) (workflow orchestration) | [vmware-policy](../vmware-policy/SKILL.md) (audit/policy)

## What This Skill Does

| Category | Capabilities | Count |
|----------|-------------|:-----:|
| **Supervisor** | Compatibility check, status, storage policies | 3 |
| **Namespace** | List, get, create with quotas, update, delete with TKC guard, VM classes | 6 |
| **TKC Clusters** | List, get, versions, create, scale, upgrade, delete with workload guard | 7 |
| **VM Service** | VM snapshots, VM groups + bootOrder, VM multi-NIC readout (vm-operator CRDs, read-only) | 3 |
| **Access** | Supervisor kubeconfig, TKC kubeconfig, Harbor registry, storage usage | 4 |

## Quick Install

```bash
uv tool install vmware-vks
vmware-vks check
```

## When to Use This Skill

- Check if vSphere environment supports VKS
- Create, update, or delete Supervisor Namespaces with resource quotas
- Deploy, scale, upgrade, or delete TKC (TanzuKubernetesCluster) clusters
- Get kubeconfig for Supervisor or TKC clusters
- Check Harbor registry info or storage usage

**Use companion skills for**:
- VM lifecycle, deployment → `vmware-aiops`
- Inventory, health, alarms → `vmware-monitor`
- iSCSI, vSAN, datastore → `vmware-storage`
- Load balancing, AVI/ALB, AKO, Ingress → `vmware-avi`

## Related Skills — Skill Routing

| User Intent | Recommended Skill |
|-------------|------------------|
| Read-only monitoring | **vmware-monitor** |
| Storage: iSCSI, vSAN | **vmware-storage** |
| VM lifecycle, deployment | **vmware-aiops** |
| vSphere Kubernetes Service (vSphere 8.x+) | **vmware-vks** ← this skill |
| NSX networking: segments, gateways, NAT | **vmware-nsx** |
| NSX security: DFW rules, security groups | **vmware-nsx-security** |
| Aria Ops: metrics, alerts, capacity planning | **vmware-aria** |
| Multi-step workflows with approval | **vmware-pilot** |
| Compliance baselines (CIS / 等保 / PCI-DSS), drift detection, LLM remediation advisor | **vmware-harden** (`uv tool install vmware-harden`) |
| Load balancer, AVI, ALB, AKO, Ingress | **vmware-avi** (`uv tool install vmware-avi`) |
| Audit log query | **vmware-policy** (`vmware-audit` CLI) |

## Common Workflows

### Deploy a New TKC Cluster

**Pre-flight (judgment)**:
- Supervisor must be vSphere 8.x+ with WCP enabled — `supervisor check` returns pass/fail. If fail, no amount of TKC commands will work; resolve at vSphere/WCP layer first.
- K8s version: pick a TKR version that's still supported by VMware (not EOL). New clusters on EOL versions look fine until you need a CVE patch and there isn't one.
- VM class sizing: `best-effort-*` for dev, `guaranteed-*` for prod. A `best-effort` worker can be evicted under host pressure — production workloads need guaranteed.
- Storage policy: must already exist in vCenter. `list_supervisor_storage_policies` first and pass the returned `policy` ID (not the display name); creating a TKC against a missing policy fails after CP boot, leaving partial state.
- Control-plane count: `1` for dev, `3` for prod (HA). Cannot upgrade from 1→3 without recreating; choose right the first time.
- Namespace quota: TKC consumes CP + worker × (cpu, memory) from namespace quota. If quota is too tight, workers fail to schedule with no obvious error.
- TKC API version: auto-detected at runtime via the K8s discovery API (prefers `cluster.x-k8s.io/v1` when the Supervisor serves it, falls back to `v1beta1` on vSphere 8.0). No manual selection needed; advanced callers can override via the `api_version` parameter on `generate_tkc_yaml()`.

**Steps**:
1. `vmware-vks supervisor check --target prod` → must pass
2. `vmware-vks tkc versions -n <ns>` → pick a non-EOL TKR
3. (If new namespace) `vmware-vks namespace create dev --storage-policy <policy> --cpu <enough-for-cp+workers> --apply --dry-run` then real
4. `vmware-vks tkc create dev-cluster -n dev --version <tkr> --control-plane 1 --workers 3 --vm-class best-effort-large --apply --dry-run` then real
5. Wait for `phase=running` (typically 10-15 min); do not assume success on apply return
6. `vmware-vks kubeconfig get dev-cluster -n dev -o ./kubeconfig` — write to file, do not paste tokens into the agent context

### Scale Workers for Load Testing

**Judgment**: scaling is fast but reverse-scaling is destructive — workers are deleted, in-flight pods lost. Treat scale-down like a delete.

1. `tkc get dev-cluster -n dev` → record current worker count and any pending pods
2. **Scale-up**: `tkc scale dev-cluster -n dev --workers 6` → safe, additive operation
3. Verify new workers reach `Ready` in `kubectl get nodes` before sending traffic
4. **Scale-down**: drain pods first via `kubectl drain` on the to-be-deleted nodes, THEN `tkc scale --workers 3`. Skipping drain causes pod restarts on remaining nodes — measurable user impact.
5. Confirm namespace quota leftover supports the new size — quota is enforced at scheduling, not at scale request

### Namespace Resource Management

**Judgment**: quota changes are atomic but consequences are not. Reducing quota below current usage doesn't evict pods — they keep running, but no new pods schedule, looking like a "namespace is broken" symptom.

1. `namespace list` → see all namespaces and their phase
2. `storage -n dev` → check current CPU/memory/storage usage; **never reduce quota below current usage + 20% headroom**
3. `namespace update dev --cpu <new> --memory <new> --dry-run` → preview, then real
4. Validate by attempting a small pod scale-up; if it pends with `Insufficient cpu`, quota is still the bottleneck

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
      TKC CR apply / get / delete  (cluster.x-k8s.io/v1beta1)
      Kubeconfig bearer token from POST /wcp/login (Supervisor JWT)
  ↓
vCenter Server 8.x+ (Workload Management enabled)
  ↓
Supervisor Cluster → vSphere Namespaces → TanzuKubernetesCluster
```

## Usage Mode

| Scenario | Recommended | Why |
|----------|:-----------:|-----|
| Local/small models (Ollama, Qwen) | **CLI** | ~2K tokens vs ~8K for MCP |
| Cloud models (Claude, GPT-4o) | Either | MCP gives structured JSON I/O |
| Automated pipelines | **MCP** | Type-safe parameters, structured output |

## MCP Tools (23 — 15 read, 8 write)

All accept optional `target` parameter to specify a named vCenter.

`list_namespaces`, `list_supervisor_storage_policies` and `list_vm_classes` return the family
list envelope — `{items, returned, limit, total, truncated, hint}` — rather than a bare array.
Read the rows from `items`; `truncated` says whether the listing is complete, so it never has
to be guessed from the row count. These three read their collection in one un-paged call, so
`total` is the real count and `truncated` is always `false`.

| Category | Tool | Type |
|----------|------|:----:|
| **Supervisor** | `check_vks_compatibility` | Read |
| | `get_supervisor_status` | Read |
| | `list_supervisor_storage_policies` | Read |
| **Namespace** | `list_namespaces` | Read |
| | `get_namespace` | Read |
| | `create_namespace` | Write |
| | `update_namespace` | Write |
| | `delete_namespace` | Write |
| | `list_vm_classes` | Read |
| **TKC** | `list_tkc_clusters` | Read |
| | `get_tkc_cluster` | Read |
| | `get_tkc_available_versions` | Read |
| | `create_tkc_cluster` | Write |
| | `scale_tkc_cluster` | Write |
| | `upgrade_tkc_cluster` | Write |
| | `delete_tkc_cluster` | Write |
| **VM Service** | `list_vm_snapshots` | Read |
| | `list_vm_groups` | Read |
| | `list_vm_network_interfaces` | Read |
| **Access** | `get_supervisor_kubeconfig` | Read |
| | `get_tkc_kubeconfig` | Write |
| | `get_harbor_info` | Read |
| | `list_namespace_storage_usage` | Read |

`create_namespace` / `create_tkc_cluster` — defaults to `dry_run=True`, returns a YAML plan for review. Pass `dry_run=False` to apply.

`delete_namespace` — requires `confirmed=True` and rejects if TKC clusters still exist (prevents orphaned clusters).

`delete_tkc_cluster` — requires `confirmed=True` and checks for running workloads. Rejects if found unless `force=True`.

**Credential handling**: `get_supervisor_kubeconfig` and `get_tkc_kubeconfig` return short-lived session tokens (not long-lived credentials). Tokens are derived from the authenticated vCenter session and expire when the session ends. Kubeconfig output is intended for local `kubectl` use — agents should write it to a file (`-o <path>`) rather than displaying tokens in conversation context.

> Full capability details and safety features: see `references/capabilities.md`

## CLI Quick Reference

```bash
# Supervisor
vmware-vks check [--target <name>]
vmware-vks preflight-auth [--target <name>]   # live-validate POST /wcp/login (issue #13)
vmware-vks supervisor status <cluster-id> [--target <name>]
vmware-vks supervisor storage-policies [--target <name>]

# Namespace
vmware-vks namespace list [--target <name>]
vmware-vks namespace get <name> [--target <name>]
vmware-vks namespace create <name> --cluster <id> [--cpu <n>] [--memory <mb>] [--storage-policy <name>] [--apply]
vmware-vks namespace update <name> [--cpu <n>] [--memory <mb>] [--target <name>]
vmware-vks namespace delete <name> [--target <name>]

# TKC Clusters
vmware-vks tkc list [-n <namespace>] [--target <name>]
vmware-vks tkc create <name> -n <ns> [--version <v>] [--workers <n>] [--vm-class <name>] [--apply]
vmware-vks tkc scale <name> -n <ns> --workers <n> [--pool <name>] [--target <name>]
vmware-vks tkc upgrade <name> -n <ns> --version <v> [--target <name>]
vmware-vks tkc delete <name> -n <ns> [--skip-workload-check] [--target <name>]

# Kubeconfig
vmware-vks kubeconfig supervisor -n <namespace> [--target <name>]
vmware-vks kubeconfig get <cluster-name> -n <namespace> [-o <path>] [--target <name>]

# Harbor & Storage
vmware-vks harbor [--target <name>]
vmware-vks storage -n <namespace> [--target <name>]
```

> Full CLI reference with all flags and interactive creation: see `references/cli-reference.md`

## Troubleshooting

### "VKS not compatible" error

Workload Management must be enabled in vCenter. Check: vCenter UI → Workload Management. Requires vSphere 8.x+ with Enterprise Plus or VCF license.

### Namespace creation fails with "storage policy not found"

List policies first: `vmware-vks supervisor storage-policies`, then pass the **Policy ID** column value (not the display name) as `--storage-policy`.

### TKC cluster stuck in "Creating" phase

Check Supervisor events in vCenter. Common causes: insufficient resources on ESXi hosts, network issues with NSX-T, or storage policy not available on target datastore.

### Every REST tool returns 401

vCenter keeps two independent session stores, and this skill uses both. Namespace, storage-policy and Supervisor-status tools call the vSphere Automation REST API under `/api`, which authenticates with a session id from `POST https://<vcenter>/api/session` (HTTP Basic on that one call, then the id in a `vmware-api-session-id` header). The pyVmomi SOAP session under `/sdk` is a different store and its key is rejected there — sending it produced a 401 on every REST tool. A 401 is refreshed automatically once; if it persists, check whether a proxy between you and vCenter strips the `vmware-api-session-id` header. A **403**, not a 401, is what an account short of Workload Management permissions gets.

### Validating Supervisor auth (POST /wcp/login)

Supervisor/TKC Kubernetes auth uses a JWT obtained from `POST https://<vcenter>/wcp/login` (HTTP Basic → JSON `session_id` bearer token), not the pyVmomi SOAP session key, and not the `/api/session` id above either — three separate credentials. To validate this end-to-end against your real Supervisor, run:

```bash
vmware-vks preflight-auth [--target <name>]
```

It performs the **real** login (no mocks) and reports, per target: vCenter reachable → `/wcp/login` HTTP status → parseable `session_id` → does the JWT authenticate a trivial Supervisor K8s API call. A healthy result is all four steps `✓ PASS` ending in `target '<name>': /wcp/login auth flow validated end-to-end.` (exit code 0). On failure each step prints a teaching message — e.g. a 404 on `/wcp/login` means the endpoint path differs on your Supervisor version (capture the real path), a 401 on the K8s probe means `session_id` is not the bearer token on your version. It never tracebacks — every failure is status output.

### Kubeconfig retrieval fails

Supervisor API endpoint must be reachable from the machine running vmware-vks. Check firewall rules for port 6443.

### Scale operation has no effect

Verify the cluster is in "Running" phase before scaling. Clusters in "Creating" or "Updating" phase reject scale operations.

### Delete namespace rejected unexpectedly

The namespace delete guard prevents deletion when TKC clusters exist inside. Delete all TKC clusters in the namespace first, then retry.

## Prerequisites

- vSphere 8.x+ with Workload Management enabled
- Enterprise Plus or VCF license
- NSX-T (recommended) or VDS + HAProxy networking
- Supervisor Cluster configured and running

## Setup

```bash
uv tool install vmware-vks
mkdir -p ~/.vmware-vks
vmware-vks init
```

> All tools are automatically audited via vmware-policy. Audit logs: `vmware-audit log --last 20`

> Full setup guide, security details, and AI platform compatibility: see `references/setup-guide.md`

## Audit & Safety

All operations are automatically audited via vmware-policy (`@vmware_tool` decorator):
- Every tool call logged to `~/.vmware/audit.db` (SQLite, framework-agnostic) with a local JSON-Lines mirror at `~/.vmware-vks/audit.log`
- Policy rules enforced via `~/.vmware/rules.yaml` (deny rules, maintenance windows, risk levels)
- Risk classification: each tool tagged as low/medium/high/critical
- View recent operations: `vmware-audit log --last 20`
- View denied operations: `vmware-audit log --status denied`

**In-memory kubeconfig (v1.5.18+)**: kubeconfig for the Supervisor and TKC clusters — which embeds the vCenter session bearer token — is built as a Python dict and loaded into the kubernetes client via `load_kube_config_from_dict()`. The token never touches disk during normal MCP/CLI flow, eliminating the previous temp-file TOCTOU window. The explicit `kubeconfig get -o <path>` CLI export still writes to the user-chosen path for `kubectl` use.

vmware-policy is automatically installed as a dependency — no manual setup needed.

## License

MIT — [github.com/vmware-skills/VMware-VKS](https://github.com/vmware-skills/VMware-VKS)
