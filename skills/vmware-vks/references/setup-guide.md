# Setup Guide

Full setup, security details, and AI platform compatibility for `vmware-vks`.

## Installation

All install methods fetch from the same source: [github.com/vmware-skills/VMware-VKS](https://github.com/vmware-skills/VMware-VKS) (MIT licensed). We recommend reviewing the source code before installing.

```bash
# Via Skills.sh (fetches from GitHub)
npx skills add vmware-skills/VMware-VKS

# Via ClawHub (fetches from ClawHub registry snapshot of GitHub)
clawhub install @zw008/vmware-vks

# Via PyPI (recommended for version pinning)
uv tool install vmware-vks==1.2.3
```

### Claude Code

`npx skills add` and `clawhub install` both place the skill in Claude Code's skills
directory. To install it manually from a clone:

```bash
mkdir -p ~/.claude/skills/vmware-vks
cp -r skills/vmware-vks/. ~/.claude/skills/vmware-vks/
```

For tool access (not just skill context), register the MCP server:

```bash
claude mcp add vmware-vks -- vmware-vks mcp
```

### What Gets Installed

The `vmware-vks` package installs a Python CLI binary and its dependencies (pyVmomi, kubernetes Python client, Typer, Rich, python-dotenv, mcp). No background services or daemons are started during installation.

### Development Install

```bash
git clone https://github.com/vmware-skills/VMware-VKS.git
cd VMware-VKS
uv venv && source .venv/bin/activate
uv pip install -e .
```

## Version Compatibility

| vSphere / VCF | Support | Notes |
|---------|---------|-------|
| 8.0 / 8.0U1-U3 | Full | Workload Management APIs available; TKC uses `cluster.x-k8s.io/v1beta1`. |
| 9.0 / 9.1 (VCF 9) | ⚠ Not yet verified | Workload Management (Supervisor / WCP) API surface in vSphere 9 has not been tested by maintainers. Existing vSphere 8.x code paths should work — basic CRUD likely works, corner cases may need testing. TKC API version is auto-detected (`v1` preferred when served, otherwise `v1beta1`). File issues with `check_vks_compatibility` output if you run this on VCF 9. |
| 7.x | Not supported | WCP API surface is different; use vSphere 8.x+. |

## Configuration

```bash
# 1. Install from PyPI
uv tool install vmware-vks

# 2. Configure
mkdir -p ~/.vmware-vks
cat > ~/.vmware-vks/config.yaml << 'EOF'
targets:
  - name: vcenter01
    host: vcenter.example.com
    username: admin@vsphere.local
    port: 443
    verify_ssl: false
    environment: production
EOF

echo "VMWARE_VKS_VCENTER01_PASSWORD=your_password" > ~/.vmware-vks/.env
chmod 600 ~/.vmware-vks/.env

# 3. Verify
vmware-vks check
```

**`environment` (optional label)**: policy scopes its rules by this value, so an environment-scoped `deny` rule in `~/.vmware/rules.yaml` can match on it — for example, to freeze state-changing writes on `production`. Any label you like works (`production`, `staging`, `lab`, `dc2-prod`); the target's *name* is not used for it.

A target with no label is simply not matched by such a rule. Read-only operations are never affected either way. Run `vmware-audit policy` to see the rules currently in force.

## MCP Mode (Optional)

For Claude Code / Cursor users who prefer structured tool calls, add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "vmware-vks": {
      "command": "vmware-vks",
      "args": ["mcp"],
      "env": {
        "VMWARE_VKS_CONFIG": "/Users/you/.vmware-vks/config.yaml",
        "VMWARE_VKS_VCENTER01_PASSWORD": "your-password"
      }
    }
  }
}
```

> v1.5.15+ recommends the single-command form `vmware-vks mcp`. Pre-1.5.15 used
> `uvx --from vmware-vks vmware-vks-mcp`, which still works but re-resolves from
> PyPI on each launch and breaks behind corporate TLS proxies. The legacy
> `vmware-vks-mcp` entry point is also kept for backward compatibility.

## Usage Mode

Choose the best mode based on your environment:

| Scenario | Recommended Mode | Why |
|----------|-----------------|-----|
| **Cloud models** (Claude, GPT-4o, Gemini) | MCP or CLI | Both work well; MCP gives structured JSON I/O |
| **Local/small models** (Ollama, Llama, Qwen <32B) | **CLI** | Lower token cost (~2K vs ~8K), higher accuracy -- small models struggle with 23 MCP tool schemas |
| **Token-sensitive workflows** | **CLI** | CLI via SKILL.md uses ~2K tokens; MCP loads ~8K tokens of tool definitions into every conversation |
| **Automated pipelines / Agent chaining** | **MCP** | Structured JSON input/output, type-safe parameters, no shell parsing |

### Calling Priority

- **MCP-native tools** (Claude Code, Cursor): MCP first, CLI fallback
- **Local models / Token-sensitive**: CLI first (MCP not needed)

### Password obfuscation at rest

On first load, any plaintext `*_PASSWORD` value in `.env` is automatically
rewritten to a grep-safe `b64:<encoded>` form and decoded transparently at
runtime, so a casual `grep` of the file no longer reveals the password. Values
are read and written through python-dotenv's own parser, so the stored secret
never drifts from what you configured (quotes, inline comments, and trailing
whitespace are handled correctly).

> **This is obfuscation, not encryption.** Anyone who can read the file can
> still decode it. For real secrecy at rest, do not store the password in `.env`
> at all — inject it from a secret manager (HashiCorp Vault, CyberArk, AWS
> Secrets Manager, or a Kubernetes Secret) into the `*_PASSWORD` environment
> variable at process start. The code reads the env var either way.

## Read-Only Operation

To run the agent read-only, give it a read-only vCenter/Supervisor service account (RBAC).

## Security

> **Disclaimer**: This is a community-maintained open-source project and is **not affiliated with, endorsed by, or sponsored by VMware, Inc. or Broadcom Inc.** "VMware" and "vSphere" are trademarks of Broadcom.

This skill follows a defense-in-depth approach with six security properties:

1. **Source Code** -- MIT-licensed, fully auditable. No obfuscated logic. Source at [github.com/vmware-skills/VMware-VKS](https://github.com/vmware-skills/VMware-VKS). The `uv` installer fetches the `vmware-vks` package from PyPI, which is built from this GitHub repository.

2. **Credentials** -- `config.yaml` contains vCenter hostnames and usernames only. Passwords are loaded exclusively from `~/.vmware-vks/.env` (read via `python-dotenv`). Passwords are never logged, never echoed to CLI output, and never included in audit log entries. **In-memory kubeconfig (v1.5.18+)**: Supervisor and TKC kubeconfigs — which embed the vCenter session bearer token — are built as a Python dict and handed to the kubernetes client via `load_kube_config_from_dict()`. The bearer token never touches disk during normal MCP/CLI flow, eliminating the previous temp-file TOCTOU window. The explicit `vmware-vks kubeconfig get -o <path>` CLI export still writes to the user-chosen path so `kubectl` can use it.

3. **Network Scope** -- No webhook, HTTP listener, or inbound network connection is ever started. MCP transport is stdio only. All outbound connections go to the user-configured vCenter host only.

4. **TLS Verification** -- `verify_ssl: false` is supported for self-signed vCenter certificates (standard in enterprise environments). Set `verify_ssl: true` in config for CA-signed certificates. Applies to both the SOAP API and REST API connections.

5. **Prompt Injection Protection** -- All tool inputs are passed as typed Python parameters (`str`, `int`, `bool`), never interpolated into shell commands. No `eval`, `exec`, or subprocess calls with user-controlled data.

6. **Least Privilege** -- 16/23 tools are read-only. All write operations default to `dry_run=True` where applicable. Destructive operations (`delete_namespace`, `delete_tkc_cluster`) require explicit `confirmed=True` and pass through safety guards that cannot be bypassed without `force=True`. All write operations are audit-logged to `~/.vmware/audit.db` (SQLite WAL, via vmware-policy).

## Supported AI Platforms

| Platform | Status |
|----------|--------|
| Claude Code | Native Skill |
| Goose (Block) | MCP via stdio |
| Cursor | MCP mode |
| Continue | MCP mode |
| VS Code Copilot | MCP mode |
| Python CLI | Standalone |
