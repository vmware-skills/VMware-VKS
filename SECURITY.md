# Security Policy

## Disclaimer

This is a community-maintained open-source project and is **not affiliated with, endorsed by, or sponsored by VMware, Inc. or Broadcom Inc.** "VMware" and "vSphere" are trademarks of Broadcom Inc.

**Author**: Wei Zhou, VMware by Broadcom — wei-wz.zhou@broadcom.com

## Reporting Vulnerabilities

If you discover a security vulnerability, please report it privately:

- **Email**: wei-wz.zhou@broadcom.com
- **GitHub**: Open a [private security advisory](https://github.com/vmware-skills/VMware-VKS/security/advisories/new)

Do **not** open a public GitHub issue for security vulnerabilities.

## Security Design

### Credential Management

- Passwords are stored exclusively in `~/.vmware-vks/.env` (never in `config.yaml`, never in code)
- `.env` is created 0600 by `vmware-vks init`; every CLI/MCP start and `vmware-vks doctor` warn if it is readable by other users
- No credentials are logged, echoed, or included in audit entries
- Each vCenter target uses a separate environment variable: `VMWARE_VKS_<TARGET_NAME_UPPER>_PASSWORD` (target `vcenter01` → `VMWARE_VKS_VCENTER01_PASSWORD`)

### Destructive Operation Safeguards

All write operations pass through multiple safety layers:

1. **`@vmware_tool` decorator** — mandatory on every MCP tool; provides pre-checks, audit logging, data sanitization, and timeout control
2. **`dry_run=True` default** — `create_namespace`, `create_tkc_cluster`, `delete_namespace` and `delete_tkc_cluster` default to dry-run; the caller must explicitly set `dry_run=False` to execute. `update_namespace`, `scale_tkc_cluster` and `upgrade_tkc_cluster` have no dry-run and apply directly
3. **`confirmed=True` required** — namespace and TKC delete operations require `confirmed=True`; without it, the operation returns a preview only
4. **Namespace deletion guard** — namespace delete is rejected if TKC clusters still exist within the namespace
5. **TKC deletion guard** — TKC delete checks for running workloads before proceeding
6. **Audit logging** — every operation (read and write) is logged to `~/.vmware/audit.db` (SQLite WAL) with timestamp, user, target, operation, parameters, and result
7. **Policy engine** — `~/.vmware/rules.yaml` can deny operations by pattern, enforce maintenance windows, and set risk-level thresholds

### Kubeconfig Security

- `get_supervisor_kubeconfig` and `get_tkc_kubeconfig` are **credential access**: the kubeconfig embeds a Supervisor bearer token (JWT from `POST /wcp/login`) that acts as the configured vCenter account until it expires — typically hours, and not revoked when vmware-vks exits
- Both MCP tools are annotated `readOnlyHint: false` (MCP clients ask before running them) and `risk_level: medium`; the matching CLI commands are `@guarded` at the same level. Call them only on explicit user request
- With `output_path` (MCP) / `-o` (CLI) the kubeconfig is written to an owner-only (0600) file — also when it replaces an existing file — and a symlink target is refused. Without it, the kubeconfig is returned inline, which puts the token into the agent's context; always prefer a file
- The audit row records the retrieval (who, when, which namespace/cluster, output path) but the returned kubeconfig is redacted (`sensitive_result=True`)
- The skill never cleans up exported kubeconfigs; delete them when no longer needed

### SSL/TLS Verification

- TLS certificate verification is **enabled by default**
- `verify_ssl: false` (per target in `config.yaml`) exists solely for vCenter/Supervisor instances using self-signed certificates in isolated lab/home environments
- In production, always use CA-signed certificates with full TLS verification

### Transitive Dependencies

- `vmware-policy` is the only transitive dependency auto-installed; it provides the `@vmware_tool` decorator and audit logging
- All other dependencies are standard Python packages (pyVmomi, Click, Rich, python-dotenv, kubernetes)
- No post-install scripts or background services are started during installation

### Prompt Injection Protection

- All vSphere-sourced content (namespace names, TKC names, cluster status messages) is processed through `_sanitize()`
- Sanitization truncates to 500 characters and strips C0/C1 control characters

## Static Analysis

This project is scanned with [Bandit](https://bandit.readthedocs.io/) before every release, targeting 0 Medium+ issues:

```bash
uvx bandit -r vmware_vks/
```

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.5.x   | Yes       |
| < 1.5   | No        |
