# Capabilities

Detailed capability breakdown for all 25 MCP tools.

## Automation Level Reference

Each operation is classified by autonomy level per the Enterprise Harness Engineering framework:

| Level | Meaning | Agent autonomy | Examples in this skill |
|:-:|---|---|---|
| **L1** | Read-only, raw data | Always auto-run | `check_vks_compatibility`, `get_supervisor_status`, `list_supervisor_storage_policies`, `list_namespaces`, `get_namespace`, TKC list/get, `get_harbor_info`, `list_namespace_storage_usage` |
| **L2** | Read + analysis / recommendation | Always auto-run | namespace quota analysis, TKC health correlation, storage policy compatibility checks |
| **Credential** | Returns live access material (bearer token) | **Never auto-run** — only on explicit user request; write to a file, report the path | `issue_supervisor_kubeconfig`, `issue_tkc_kubeconfig` |
| **L3** | Single write — user must approve | Authorized request; MCP preview gates and CLI confirmation are separate (see Notes) | `create_namespace`, `update_namespace`, `delete_namespace`, `create_tkc_cluster`, `upgrade_tkc_cluster`, `scale_tkc_cluster`, `delete_tkc_cluster` |
| **L4** | Multi-step plan / apply workflow | Plan generation auto; apply gated by user approval | *(roadmap — TKC fleet upgrades, multi-namespace bootstrapping plans)* |
| **L5** | Auto-remediation from learned pattern | Pattern library only; requires `risk:low` + `reversible:true` + `repeatable:true` | *(roadmap — candidates: stuck TKC reconciliation, namespace quota bumps)* |

**Notes**:
- L1/L2 are infrastructure reads under the configured account; Policy, RBAC and audit still apply. Read access may reveal sensitive inventory. Credential-returning tools are separate and require an explicit authorized request. Their `readOnlyHint: false` annotation is a client hint; it does not enforce a prompt or independent human approval.
- L3 tools always pass through the `@vmware_tool` decorator: Policy and audit apply to every call. MCP `create_namespace`, `delete_namespace`, `create_tkc_cluster` and `delete_tkc_cluster` preview until `confirm=true`; namespace update, scale and upgrade act on their first authorized invocation. CLI destructive commands separately double-confirm.
- Kubeconfig retrieval is credential access. The kubeconfig embeds a Supervisor bearer token (JWT from `/wcp/login`) that acts as the configured vCenter account until it expires — typically hours, not tied to the vmware-vks process. Both tools are `readOnlyHint: false`, `risk_level: medium`, and `sensitive_result=True` (the audit row records the call; the returned kubeconfig is redacted). Always pass `output_path` (MCP) / `-o <path>` (CLI): the file is created owner-only (0600), including when it replaces an existing file, and a symlink target is refused. `output_path` truncates the named file, so `~/.kube/config` would be replaced.

## 1. Supervisor Layer (Read-Only)

| Tool | What it returns |
|------|----------------|
| `check_vks_compatibility` | vCenter version (pass/fail for 8.x+), WCP enabled status, network backend type |
| `get_supervisor_upgrade_risk` | vCenter 9.1.0/9.1.1 advisory applicability, source build, Supervisor inventory, and official checker link. `vumdb_schema_status=unmeasured` and `upgrade_clearance=not_established` always; run Broadcom KB 455737 checker or VDT on affected sources before upgrading. |
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
| Create | `namespace create <name> --apply` | `create_namespace` | `confirm` | Preview first; CPU/memory quotas, storage policy |
| Update quotas | `namespace update <name>` | `update_namespace` | -- | CPU (MHz), memory (MB) |
| Delete | `namespace delete <name>` | `delete_namespace` | `confirm` | Preview lists TKC clusters, VMs, PVCs; rejects if TKC clusters exist |
| VM classes | `namespace vm-classes` | `list_vm_classes` | -- | `id`, `cpu_count`, `memory_mb`, `gpu_count` (derived from vGPU + dynamic DirectPath I/O device lists) |

## 3. TKC Layer

| Operation | CLI | MCP Tool | Confirmation | Details |
|-----------|-----|----------|:------------:|---------|
| List clusters | `tkc list [-n ns]` | `list_tkc_clusters` | -- | Status, node counts, K8s version |
| Get detail | `tkc get <name> -n <ns>` | `get_tkc_cluster` | -- | All declared worker pools, known fixed-replica total, version, health conditions |
| Available versions | `tkc versions -n <ns>` | `get_tkc_available_versions` | -- | Supported K8s versions for Supervisor |
| Create | `tkc create <name> -n <ns> --apply` | `create_tkc_cluster` | `confirm` | YAML plan -> confirm -> apply |
| Scale workers | `tkc scale <name> -n <ns> --workers N` | `scale_tkc_cluster` | -- | Adjust worker node count |
| Upgrade | `tkc upgrade <name> -n <ns> --version X.Y` | `upgrade_tkc_cluster` | -- | List available versions first |
| Delete | `tkc delete <name> -n <ns>` | `delete_tkc_cluster` | `confirm` | Preview lists nodes and workloads; rejects if workloads running |

`get_tkc_cluster.worker_pools` contains every declared pool. `worker_replica_total`
is `null` when any pool count is autoscaled, missing, or invalid; it is not an
observed live node count. `worker_replicas` remains the first pool's declared
count for older clients. VKS 3.8 adds vendor-managed chained upgrades and node
placement, but this skill has not verified 3.8 APIs or the chain in a real 9.1
lab. `upgrade_tkc_cluster` submits one version patch only; do not infer upgrade
clearance or placement support from this read-only summary. See the
[VKS 3.8 release announcement](https://blogs.vmware.com/cloud-foundation/2026/11/02/vks-3-8-advancing-ai-modern-workloads-private-cloud/).

### Measured Cluster API schema support

**Available from skill 3.7.0. Earlier 3.6.1 supports v1beta1 only. Use these
parameters with a 3.7.0 or newer server.**

Every live cluster operation discovers the served `cluster.x-k8s.io` versions for its
current session and namespace. The skill uses `v1beta1` when available to preserve
existing behavior, otherwise `v1beta2`. Unknown-only versions, failed discovery,
authorization failures and malformed replies stop the operation; no cached fallback
or guessed `v1` is used.

Creation accepts `cluster_class` and `cluster_class_namespace` (CLI: `--cluster-class`
and `--cluster-class-namespace`). Select an installed, compatible class; shared VKS
classes commonly live in `vmware-system-vks-public`. `v1beta2` requires an explicit
class. The `v1beta1` legacy default remains `tanzukubernetescluster` for compatibility;
it is deprecated by Broadcom and may be absent. Prefer an explicit installed class.
The template supplies `vmClass`, `storageClass` and one `node-pool`; custom classes
requiring other variables or worker classes need their own manifest.

`v1beta1` emits `topology.class`/`classNamespace` and duration strings;
`v1beta2` emits `topology.classRef` and `deletion.nodeDrainTimeoutSeconds`.
Readiness uses current conditions, with legacy booleans as fallback; absent/Unknown
readiness is `null`, not a healthy measurement. The legacy phase may be absent;
inspect conditions on modern clusters. Existing topology and all pools remain
preserved during scaling; upgrade changes the Kubernetes version without rebasing the class.

CLI create dry-runs generate **unvalidated offline templates**; `--api-version` selects
that template's schema only. MCP create previews use live discovery and name checks,
then Supervisor admission (`dryRun=All`, strict field validation). Apply repeats
admission before persistence. Rejected class/variables, unavailable dry-run webhooks,
RBAC failures or schema errors stop creation. Admission success does not prove that
nodes will provision or that guest access works. No automatic class selection,
variable conversion, class rebase or upgrade clearance is provided.

This is an API/schema adaptation, **not end-to-end VKS/VCF 9.1 lab validation**.
Issue [#27](https://github.com/vmware-skills/VMware-VKS/issues/27)'s guest kubeconfig
401 remains a separate access-validation concern.

Sources: [Broadcom VKS API](https://developer.broadcom.com/xapis/vmware-vsphere-kubernetes-service/latest/api-docs.html),
[variable reference](https://developer.broadcom.com/xapis/vmware-vsphere-kubernetes-service/latest/variable-docs.html),
[legacy class deprecation](https://knowledge.broadcom.com/external/article/410526/guest-cluster-tkr-install-or-upgrade-to.html),
[shared class namespace](https://knowledge.broadcom.com/external/article/416281/clusterclass-builtingenericv340-only-exi.html).

## 4. Access Layer

| Tool | What it returns |
|------|----------------|
| `issue_supervisor_kubeconfig` | **Credential.** Kubeconfig for the Supervisor K8s API (bearer token); inline or written to an owner-only file |
| `issue_tkc_kubeconfig` | **Credential.** Kubeconfig for one TKC cluster (bearer token); inline or written to an owner-only file |
| `get_harbor_info` | Per registry: `id`, `cluster`, `version`, `url`, `status` (health), `storage_used_mb` — status/storage come from a per-registry detail call and are null if it fails. Never returns credentials |
| `list_namespace_storage_usage` | PVC list and usage stats per Namespace |

## 5. VM Service (vm-operator)

| Tool | What it returns |
|------|----------------|
| `list_vm_service_snapshots` | VirtualMachineSnapshot objects in a vSphere Namespace (Kubernetes VM Service — not vSphere VM snapshots, which are vmware-aiops/vmware-monitor `list_vm_snapshots`) |
| `list_vm_service_groups` | VirtualMachineGroup objects and their boot order in a Namespace |
| `list_vm_service_network_interfaces` | Network interfaces (multi-NIC) of one VM Service VirtualMachine |

## Permissions (1 tool, read-only)

| Tool | Description |
|------|-------------|
| `get_access_info` | Which account this server uses on a target, and whether it holds the vSphere privileges each tool needs. `tool` (optional; omit for every tool), `target` |

- Asks vCenter about the current session with `AuthorizationManager.HasPrivilegeOnEntities`, which needs only System.View, so any account can ask about itself. CLI: `vmware-vks access-info [--tool NAME] [--target T]`.
- **Root folder only.** `not_granted_at_root` is not "no access": the privilege may be granted on a specific cluster. `undetermined` means vCenter did not answer; it is never read as granted.
- **What it cannot see.** This skill works through the vSphere Automation REST API and the Supervisor Kubernetes API, not SOAP. The per-tool table (`vmware_vks/access_requirements.py`) lists System.Read for inventory reads and StorageProfile.View for storage policies. Namespace create/update/delete also need Namespaces.Configure (per Broadcom's Automation API reference; not yet verified against a live vCenter's privilege list). TKC, VM Service, storage-usage and kubeconfig calls are authorized by the namespace's view/edit/owner permission, which a root-folder privilege check cannot answer, so those tools always report `undetermined` — never `granted`.
- **Typical response**: ~150 tokens for one tool; ~500 tokens for the whole-server summary.

## Safety Features

| Feature | Details |
|---------|---------|
| Plan -> Confirm -> Execute -> Log | Structured workflow: show YAML plan, confirm, execute, audit log |
| Preview Default | The two creates and two deletes take `confirm` (default `False`) -- a call without it returns `blast_radius` and changes nothing |
| Refusal When Unmeasured | `confirm=True` is refused while a blocker stands or when any part of the blast radius could not be read; the old `confirmed` / `dry_run` aliases were removed in 3.4.2 and are refused as unknown arguments |
| Namespace Delete Guard | Rejects if TKC clusters exist inside -- prevents orphaned clusters |
| TKC Delete Guard | Rejects if Deployments/StatefulSets/DaemonSets are running -- prevents data loss |
| Force Override | `force=True` on `delete_tkc_cluster` bypasses workload guard (explicit acknowledgement) |
| Audit Trail | Every MCP call and every CLI command that reaches vCenter or the Supervisor logged to `~/.vmware/audit.db` (SQLite WAL, via vmware-policy); write operations also mirrored to `~/.vmware-vks/audit.log`, with timestamp, target, operation, parameters, result, user |
| Read-Only Majority | 16/25 tools are read-only |
| SSL Support | Verification on by default; `verify_ssl: false` only for an isolated lab vCenter with a self-signed cert |
| In-Memory Kubeconfig | For the skill's own API calls the Supervisor/TKC kubeconfig is constructed as a Python dict and loaded via `load_kube_config_from_dict()`; the Supervisor bearer token is never written to a temp file (the pre-v1.5.18 TOCTOU window is gone). Only an explicit export (`output_path` / `-o <path>`) writes it, to an owner-only file. |

## Version Compatibility

| vSphere Version | TKC API | Support |
|----------------|---------|---------|
| 8.0 / 8.0U1-U3 | `cluster.x-k8s.io/v1beta1` (ClusterClass) | Full |
| 9.0 / 9.1 (VCF 9) | Discovers served `v1beta1` or `v1beta2`; explicit class and admission required for modern creation | ⚠ Partial external 9.1 report — Supervisor login works at the Supervisor endpoint, but the guest TKC API rejected this skill's kubeconfig with 401 ([#27](https://github.com/vmware-skills/VMware-VKS/issues/27)). Maintainers have not verified the full API surface; check guest access separately. |
| 7.0 U3 | `run.tanzu.vmware.com/v1alpha3` | Not supported |
| 7.0 U1-U2 | `run.tanzu.vmware.com/v1alpha1` | Not supported |

> This skill targets vSphere 8.x+ exclusively. vSphere 7.x uses a different TKC API version -- use `kubectl` directly for 7.x environments. TKC API version is auto-detected at runtime (see "TKC API Version Auto-Detection" above).

## Prerequisites

- vCenter Server (no direct ESXi support -- VKS requires vCenter)
- vSphere Kubernetes Service license (Enterprise Plus or VCF)
- Workload Management (WCP) enabled on at least one cluster
- Network backend: NSX (recommended) or VDS + Avi Networks (7.x alternative, 8.x limited)
