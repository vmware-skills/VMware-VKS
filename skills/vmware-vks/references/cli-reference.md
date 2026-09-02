# CLI Reference

Full command reference for `vmware-vks` CLI.

All commands accept an optional `--target <name>` parameter to specify a named vCenter from your config.

## Pre-flight Check

```bash
vmware-vks check   # or: vmware-vks doctor — same checks, both names
```

Verifies connectivity, credentials, and WCP status for all configured vCenters (or a specific target).

## Supervisor

```bash
# Get Supervisor cluster status (ID, API endpoint, K8s version, state)
vmware-vks supervisor status <cluster-id> [--target <name>]

# List vCenter storage policies (Policy ID / Name / Description).
# Pass the Policy ID — not the display name — when creating a Namespace or TKC.
vmware-vks supervisor storage-policies [--target <name>]
```

## Namespace

```bash
# List all vSphere Namespaces
vmware-vks namespace list [--target <name>]

# Get Namespace detail (quotas, storage bindings, role bindings)
vmware-vks namespace get <name> [--target <name>]

# Create Namespace with resource quotas and storage policy
# Defaults to dry-run (shows plan). Pass --apply to execute.
vmware-vks namespace create <name> --cluster <id> \
  [--cpu <mhz>] [--memory <mb>] \
  [--storage-policy <name>] [--apply]

# Update Namespace CPU/memory quotas
vmware-vks namespace update <name> \
  [--cpu <mhz>] [--memory <mb>] [--target <name>]

# Delete Namespace (rejects if TKC clusters exist inside)
vmware-vks namespace delete <name> [--target <name>]

# List available VM classes for TKC nodes (ID / CPU / Memory (MB) / GPU)
vmware-vks namespace vm-classes [--target <name>]
```

## TKC (TanzuKubernetesCluster)

```bash
# List TKC clusters (all namespaces or specific)
vmware-vks tkc list [-n <namespace>] [--target <name>]

# Get TKC cluster detail (nodes, versions, health conditions)
vmware-vks tkc get <cluster-name> -n <namespace> [--target <name>]

# List available K8s versions for a namespace
vmware-vks tkc versions -n <namespace> [--target <name>]

# Create TKC cluster (defaults to dry-run, pass --apply to execute)
vmware-vks tkc create <cluster-name> -n <namespace> \
  [--version <k8s-ver>] \
  [--control-plane <n>] [--workers <n>] \
  [--vm-class <name>] [--storage-policy <name>] \
  [--apply]

# Scale worker node count
vmware-vks tkc scale <cluster-name> -n <namespace> \
  --workers <n> [--target <name>]

# Upgrade TKC cluster to a newer K8s version
vmware-vks tkc upgrade <cluster-name> -n <namespace> \
  --version <k8s-ver> [--target <name>]

# Delete TKC cluster (rejects if workloads running, use --force to override)
vmware-vks tkc delete <cluster-name> -n <namespace> \
  [--skip-workload-check] [--target <name>]
# Asks you to type the cluster name back before it deletes. No bypass flag;
# --skip-workload-check only skips the running-workload guard.
```

## Kubeconfig

```bash
# Get Supervisor-level kubeconfig
vmware-vks kubeconfig supervisor -n <namespace> [--target <name>]

# Get TKC cluster kubeconfig (stdout or write to file)
vmware-vks kubeconfig get <cluster-name> -n <namespace> \
  [-o <output-path>] [--target <name>]
```

## Harbor & Storage

```bash
# Get Harbor registry info (ID, cluster, version, UI URL, health status,
# storage used in MB; status/storage are null if the detail call fails)
vmware-vks harbor [--target <name>]

# List PVC usage statistics per Namespace
vmware-vks storage -n <namespace> [--target <name>]
```

## Interactive TKC Creation

When parameters are missing, the CLI guides interactively:

```
$ vmware-vks tkc create my-cluster -n dev
? K8s version (v1.27 / v1.28 / v1.29): v1.28
? VM class (best-effort-small / best-effort-large / guaranteed-large): best-effort-large
? Control plane nodes (1 / 3): 1
? Worker nodes [3]: 3
? Storage policy (vsphere-storage / vsphere-gold): vsphere-storage

Plan:
  Cluster   : my-cluster
  Namespace : dev
  K8s       : v1.28.4+vmware.1
  Control   : 1x best-effort-large
  Workers   : 3x best-effort-large
  Storage   : vsphere-storage

Apply? [y/N]: y
```

The same guided flow applies in MCP: the AI model collects missing params through follow-up questions before generating the YAML and applying.
