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
- `.env` file permissions are verified at startup (`chmod 600` required)
- No credentials are logged, echoed, or included in audit entries
- Each vCenter target uses a separate environment variable: `VMWARE_VKS_<TARGET_NAME_UPPER>_PASSWORD` (target `vcenter01` → `VMWARE_VKS_VCENTER01_PASSWORD`)

### Destructive Operation Safeguards

All write operations pass through multiple safety layers:

1. **`@vmware_tool` decorator** — mandatory on every MCP tool; provides pre-checks, audit logging, data sanitization, and timeout control
2. **`dry_run=True` default** — all write operations default to dry-run mode; the caller must explicitly set `dry_run=False` to execute
3. **`confirmed=True` required** — namespace and TKC delete operations require `confirmed=True`; without it, the operation returns a preview only
4. **Namespace deletion guard** — namespace delete is rejected if TKC clusters still exist within the namespace
5. **TKC deletion guard** — TKC delete checks for running workloads before proceeding
6. **Audit logging** — every operation (read and write) is logged to `~/.vmware/audit.db` (SQLite WAL) with timestamp, user, target, operation, parameters, and result
7. **Policy engine** — `~/.vmware/rules.yaml` can deny operations by pattern, enforce maintenance windows, and set risk-level thresholds

### Kubeconfig Security

- `get_supervisor_kubeconfig` and `get_tkc_kubeconfig` return **short-lived vCenter session tokens**, not persistent credentials
- Kubeconfig content is never written to disk by MCP tools — it is returned in-memory to the calling agent
- Session tokens expire according to vCenter SSO policy (default 300 seconds)

### SSL/TLS Verification

- TLS certificate verification is **enabled by default**
- `disableSslCertValidation: true` exists solely for vCenter/Supervisor instances using self-signed certificates in isolated lab/home environments
- In production, always use CA-signed certificates with full TLS verification

### Transitive Dependencies

- `vmware-policy` is the only transitive dependency auto-installed; it provides the `@vmware_tool` decorator and audit logging
- All other dependencies are standard Python packages (pyVmomi, Click, Rich, python-dotenv, kubernetes)
- No post-install scripts or background services are started during installation

### Prompt Injection Protection

- All vSphere-sourced content (namespace names, TKC names, cluster status messages) is processed through `_sanitize()`
- Sanitization truncates to 500 characters and strips C0/C1 control characters
- Output is wrapped in boundary markers when consumed by LLM agents

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
