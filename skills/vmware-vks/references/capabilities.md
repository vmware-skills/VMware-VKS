# Capabilities

Detailed capability breakdown for all 23 MCP tools.

## Automation Level Reference

Each operation is classified by autonomy level per the Enterprise Harness Engineering framework:

| Level | Meaning | Agent autonomy | Examples in this skill |
|:-:|---|---|---|
| **L1** | Read-only, raw data | Always auto-run | `check_vks_compatibility`, `get_supervisor_status`, `list_supervisor_storage_policies`, `list_namespaces`, `get_namespace`, TKC list/get, `get_supervisor_kubeconfig` |
| **L2** | Read + analysis / recommendation | Always auto-run | namespace quota analysis, TKC health correlation, storage policy compatibility checks |
| **L3** | Single write — user must approve | Only after explicit confirmation; destructive ops require double-confirm + `--dry-run` | `create_namespace`, `update_namespace`, `delete_namespace`, `create_tkc_cluster`, `upgrade_tkc_cluster`, `scale_tkc_cluster`, `delete_tkc_cluster` |
| **L4** | Multi-step plan / apply workflow | Plan generation auto; apply gated by user approval | *(roadmap — TKC fleet upgrades, multi-namespace bootstrapping plans)* |
| **L5** | Auto-remediation from learned pattern | Pattern library only; requires `risk:low` + `reversible:true` + `repeatable:true` | *(roadmap — candidates: stuck TKC reconciliation, namespace quota bumps)* |

**Notes**:
- L1/L2 tools are always safe for agents to call without confirmation.
- L3 tools always pass through the `@vmware_tool` decorator: connection check → policy check → audit log → double-confirm.
- Kubeconfig retrieval returns short-lived session tokens; agents should write to file (`-o <path>`) rather than displaying tokens in conversation context. `get_tkc_kubeconfig` is **not** L1 for that reason — `output_path` truncates a caller-chosen file, so it is annotated `readOnlyHint: false` and a client should confirm it.

## 1. Supervisor Layer (Read-Only)

| Tool | What it returns |
|------|----------------|
| `check_vks_compatibility` | vCenter version (pass/fail for 8.x+), WCP enabled status, network backend type |
| `get_supervisor_status` | Cluster ID, config status, Kubernetes status, API endpoint URL, network provider, and `kubernetes_version` (read from the `software/clusters` endpoint; null with a `kubernetes_version_hint` if that call fails) |
| `list_supervisor_storage_policies` | List envelope; `items` holds vCenter storage policies: `policy` (ID), `name`, `description`. Pass the `policy` ID (not the display name) when creating a Namespace or TKC |

**List envelope**: `list_supervisor_storage_policies`, `list_namespaces` and `list_vm_classes`
return `{items, returned, limit, total, truncated, hint}` instead of a bare array, so an agent
can tell a complete answer from a first page rather than inferring it (VMware-AIops issue #31).
All three fetch their collection in a single un-paged REST call, so `total` is the real count
and `truncated` is always `false`.

## 2. Namespace Layer

| Operation | CLI | MCP Tool | Confirmation | Details |
|-----------|-----|----------|:------------:|---------|
| List all | `namespace list` | `list_namespaces` | -- | Status, resource usage, phase |
| Get detail | `namespace get <name>` | `get_namespace` | -- | Quotas, storage bindings, role bindings |
| Create | `namespace create <name> --apply` | `create_namespace` | dry_run | CPU/memory quotas, storage policy |
| Update quotas | `namespace update <name>` | `update_namespace` | -- | CPU (MHz), memory (MB) |
| Delete | `namespace delete <name>` | `delete_namespace` | Double | Rejects if TKC clusters exist |
| VM classes | `namespace vm-classes` | `list_vm_classes` | -- | `id`, `cpu_count`, `memory_mb`, `gpu_count` (derived from vGPU + dynamic DirectPath I/O device lists) |

## 3. TKC Layer

| Operation | CLI | MCP Tool | Confirmation | Details |
|-----------|-----|----------|:------------:|---------|
| List clusters | `tkc list [-n ns]` | `list_tkc_clusters` | -- | Status, node counts, K8s version |
| Get detail | `tkc get <name> -n <ns>` | `get_tkc_cluster` | -- | Nodes, versions, health conditions |
| Available versions | `tkc versions -n <ns>` | `get_tkc_available_versions` | -- | Supported K8s versions for Supervisor |
| Create | `tkc create <name> -n <ns> --apply` | `create_tkc_cluster` | dry_run | YAML plan -> confirm -> apply |
| Scale workers | `tkc scale <name> -n <ns> --workers N` | `scale_tkc_cluster` | -- | Adjust worker node count |
| Upgrade | `tkc upgrade <name> -n <ns> --version X.Y` | `upgrade_tkc_cluster` | -- | List available versions first |
| Delete | `tkc delete <name> -n <ns>` | `delete_tkc_cluster` | Double | Rejects if workloads running |

### TKC API Version Auto-Detection (v1.5.18+)

All TKC operations resolve the `cluster.x-k8s.io` API version at runtime via the Kubernetes discovery API (`/apis`). `_resolve_tkc_version()` walks the Supervisor's served versions for the `cluster.x-k8s.io` group and picks the first match from the preference order:

1. `v1` — used when the Supervisor has promoted Cluster API to v1 (later vSphere / VCF releases).
2. `v1beta1` — fallback for vSphere 8.0, which is also the default for `generate_tkc_yaml()` when called without an explicit `api_version`.

The result is cached per vCenter host, so the discovery call happens at most once per session. If discovery fails (e.g. network blip), the code logs a warning and falls back to `v1beta1` rather than throwing.

**Override** — `generate_tkc_yaml()` accepts an optional `api_version` parameter; pass `"v1"` (or any future version) explicitly when you want to pin a particular API surface for a generated TKC manifest. Most callers do not need this — auto-detection is the supported path.

## 4. Access Layer

| Tool | What it returns |
|------|----------------|
| `get_supervisor_kubeconfig` | Kubeconfig for Supervisor-level K8s API |
| `get_tkc_kubeconfig` | Kubeconfig for a specific TKC cluster (stdout or write to file) |
| `get_harbor_info` | Per registry: `id`, `cluster`, `version`, `url`, `status` (health), `storage_used_mb` — status/storage come from a per-registry detail call and are null if it fails. Never returns credentials |
| `list_namespace_storage_usage` | PVC list and usage stats per Namespace |

## Safety Features

| Feature | Details |
|---------|---------|
| Plan -> Confirm -> Execute -> Log | Structured workflow: show YAML plan, confirm, execute, audit log |
| Dry-Run Default | `create_namespace` and `create_tkc_cluster` default to `dry_run=True` -- returns plan without applying |
| Double Confirmation | Delete ops require `confirmed=True` parameter |
| Namespace Delete Guard | Rejects if TKC clusters exist inside -- prevents orphaned clusters |
| TKC Delete Guard | Rejects if Deployments/StatefulSets/DaemonSets are running -- prevents data loss |
| Force Override | `force=True` on `delete_tkc_cluster` bypasses workload guard (explicit acknowledgement) |
| Audit Trail | All write operations logged to `~/.vmware/audit.db` (SQLite WAL, via vmware-policy) plus a local JSON-Lines mirror at `~/.vmware-vks/audit.log`, with timestamp, target, operation, parameters, result, user |
| Read-Only Majority | 15/23 tools are read-only |
| SSL Support | `verify_ssl: false` supported for self-signed vCenter certs (enterprise standard) |
| In-Memory Kubeconfig | Supervisor/TKC kubeconfig is constructed as a Python dict and loaded into the kubernetes client via `load_kube_config_from_dict()`. The vCenter session bearer token never persists to disk during MCP/CLI calls — eliminates the temp-file TOCTOU window present pre-v1.5.18. Explicit `kubeconfig get -o <path>` export still writes to the user-chosen file for downstream `kubectl` use. |

## Version Compatibility

| vSphere Version | TKC API | Support |
|----------------|---------|---------|
| 8.0 / 8.0U1-U3 | `cluster.x-k8s.io/v1beta1` (ClusterClass) | Full |
| 9.0 / 9.1 (VCF 9) | `cluster.x-k8s.io/v1` preferred (auto-detected), `v1beta1` fallback | ⚠ Not yet verified — Workload Management API surface in vSphere 9 has not been tested by maintainers. Existing 8.x code paths should work but corner cases may need testing. File issues with `check_vks_compatibility` output if you run this on VCF 9. |
| 7.0 U3 | `run.tanzu.vmware.com/v1alpha3` | Not supported |
| 7.0 U1-U2 | `run.tanzu.vmware.com/v1alpha1` | Not supported |

> This skill targets vSphere 8.x+ exclusively. vSphere 7.x uses a different TKC API version -- use `kubectl` directly for 7.x environments. TKC API version is auto-detected at runtime (see "TKC API Version Auto-Detection" above).

## Prerequisites

- vCenter Server (no direct ESXi support -- VKS requires vCenter)
- vSphere Kubernetes Service license (Enterprise Plus or VCF)
- Workload Management (WCP) enabled on at least one cluster
- Network backend: NSX (recommended) or VDS + Avi Networks (7.x alternative, 8.x limited)
