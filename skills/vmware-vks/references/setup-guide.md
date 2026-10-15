# Setup Guide

Full setup, security details, and AI platform compatibility for `vmware-vks`.

## Installation

All install methods fetch from the same source: [github.com/vmware-skills/VMware-VKS](https://github.com/vmware-skills/VMware-VKS) (MIT licensed). We recommend reviewing the source code before installing.

```bash
# Via Skills.sh (fetches from GitHub)
npx skills add vmware-skills/VMware-VKS#v3.2.0

# Via ClawHub (fetches from ClawHub registry snapshot of GitHub)
clawhub install @zw008/vmware-vks --version 3.2.0

# Via PyPI (recommended for version pinning)
uv tool install vmware-vks==3.2.0
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
git clone --branch v3.2.0 https://github.com/vmware-skills/VMware-VKS.git
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
uv tool install vmware-vks==3.2.0

# 2. Configure
mkdir -p ~/.vmware-vks
cat > ~/.vmware-vks/config.yaml << 'EOF'
targets:
  - name: vcenter01
    host: vcenter.example.com
    username: admin@vsphere.local
    port: 443
    verify_ssl: true    # the default; keep it on (see TLS Verification below)
    environment: production
EOF

echo "VMWARE_VKS_VCENTER01_PASSWORD=your_password" > ~/.vmware-vks/.env
chmod 600 ~/.vmware-vks/.env

# 3. Verify
vmware-vks check
```

`supervisor_host` (optional, per target): the Supervisor API address. It is used for the Kubernetes login
(`POST https://<supervisor>/wcp/login`, port 443) and for every Supervisor API call. When unset, both go to the
endpoint vCenter publishes for the running Supervisor. On vSphere 9.1, vCenter itself answers 404 for
`/wcp/login`; only the Supervisor serves it. Set `supervisor_host` when the published endpoint is not reachable
from the machine running the skill. `vmware-vks preflight-auth` reports which address it used.

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
> `uvx --from vmware-vks vmware-vks-mcp`, which still works but re-resolves from <!-- install-pin: historical -->
> PyPI on each launch and breaks behind corporate TLS proxies. The legacy
> `vmware-vks-mcp` entry point is also kept for backward compatibility.

## Limiting which tools are offered

By default the MCP server offers every tool. To offer only read tools, start it with `--expose read`, or set
`VMWARE_VKS_MCP_EXPOSE=read` in the MCP client's `env` block (useful when the client launches `vmware-vks mcp` itself). To offer
chosen write tools as well, add `--allow-write` with a comma-separated list (or `VMWARE_VKS_MCP_ALLOW_WRITE`):

```bash
vmware-vks mcp --expose read --allow-write create_namespace
```

- A read tool is one annotated `readOnlyHint: true`; every other tool counts as a write.
- An unknown setting, or a name that is not a write tool of this server, stops the server before it serves (exit
  code 2), so it never offers more than you asked for. Pre-2.0 tool names are accepted.
- With `--expose read`, the server prints one line to stderr saying what it exposes.
- Flags and environment variables are not mixed: if either flag is given, both environment variables are
  ignored, so a leftover `_ALLOW_WRITE` in the shell cannot widen `--expose read`. A blank flag value counts as
  not given.
- A tool that runs other steps (a plan, workflow or batch tool) carries every write it can execute, including
  writes this setting hides. Expose such a tool only if you would expose those writes too.
- A launcher that imports the server object can call
  `vmware_policy.exposure.restrict_tools(mcp, skill="vks", expose="read", allow_write=[...])` before running it,
  instead of editing the SDK's tool registry.
- This limits what clients are offered. It is **not** authorization: whether a call may change anything is decided by
  the account's permissions, and deny rules in `~/.vmware/rules.yaml` still apply to every exposed tool.
- An agent that can run shell commands can also reach the platform without MCP (SSH, curl, govc, PowerCLI). So
  the service account itself must be least-privilege: exposure and deny rules are guardrails, not the boundary.

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

### Local files, permissions and retention

Everything this skill keeps on disk is sensitive. Nothing is deleted
automatically except audit-DB archives beyond the newest five.

| Path | Contents | Permissions | Retention |
|---|---|---|---|
| `~/.vmware-vks/.env` | Per-target passwords (`b64:`-obfuscated, not encrypted) | Created 0600 by `vmware-vks init`; `vmware-vks doctor` and every CLI/MCP start warn if it is wider | Until you remove it |
| `~/.vmware-vks/config.yaml` | Hostnames, usernames, `verify_ssl` — no passwords | Your umask | Until you remove it |
| `~/.vmware/audit.db` (+ `-wal`, `-shm`) | Every MCP tool call and every `@guarded` CLI command: tool, parameters, result (credentials redacted), status, OS user | 0600, directory 0700 | Rotated at 100 MB; the 5 newest archives are kept |
| `~/.vmware-vks/audit.log` | JSON-Lines mirror of namespace/TKC write operations | 0600, directory 0700 | Never rotated or pruned |
| Exported kubeconfig (`output_path` / `-o`) | Supervisor bearer token, valid until the JWT expires (typically hours) | 0600, also when replacing an existing file; symlink targets refused | Never cleaned up — delete it when done |

Prune the audit files according to your own retention policy; for the kubeconfig,
prefer a short-lived path and delete it after use.

## Read-Only Operation

To run the agent read-only, give it a read-only vCenter/Supervisor service account (RBAC).

## Security

> **Disclaimer**: This is a community-maintained open-source project and is **not affiliated with, endorsed by, or sponsored by VMware, Inc. or Broadcom Inc.** "VMware" and "vSphere" are trademarks of Broadcom.

This skill follows a defense-in-depth approach with six security properties:

1. **Source Code** -- MIT-licensed, fully auditable. No obfuscated logic. Source at [github.com/vmware-skills/VMware-VKS](https://github.com/vmware-skills/VMware-VKS). The `uv` installer fetches the `vmware-vks` package from PyPI, which is built from this GitHub repository.

2. **Credentials** -- `config.yaml` contains vCenter hostnames and usernames only. Passwords are loaded exclusively from `~/.vmware-vks/.env` (read via `python-dotenv`). Passwords are never logged, never echoed to CLI output, and never included in audit log entries. **Kubeconfig retrieval is credential access**: `issue_supervisor_kubeconfig` / `issue_tkc_kubeconfig` (CLI: `kubeconfig supervisor` / `kubeconfig get`) return a kubeconfig embedding a Supervisor bearer token (JWT from `POST /wcp/login`) that acts as the configured vCenter account until it expires — typically hours, not tied to the vmware-vks process. Both MCP tools are annotated `readOnlyHint: false` and `risk_level: medium`; run them only on explicit user request and always export with `output_path` / `-o <path>` (owner-only 0600 file) instead of printing the token. Their audit rows redact the returned kubeconfig. **In-memory kubeconfig (v1.5.18+)**: for the skill's own API calls the kubeconfig is built as a Python dict and handed to the kubernetes client via `load_kube_config_from_dict()`, so the token is never written to a temp file; only an explicit export writes it to disk.

3. **Network Scope** -- No webhook, HTTP listener, or inbound network connection is ever started. MCP transport is stdio only. Outbound connections go to the user-configured vCenter, to the Supervisor Kubernetes API endpoint that vCenter reports for the cluster (`api_server_cluster_endpoint`), and — for `delete_tkc_cluster`'s running-workload check only — to that TKC cluster's control-plane endpoint as recorded on the Supervisor.

4. **TLS Verification** -- On by default (`verify_ssl: true`), for both the SOAP API and REST API connections. Keep it on: the account is privileged, and with verification off anyone on the network path can impersonate vCenter and capture its password. For a certificate from a private CA, add that CA to the trust store (or point `SSL_CERT_FILE` at a PEM bundle containing it) instead of turning verification off. `verify_ssl: false` is for an isolated lab vCenter with a self-signed certificate only, never for a production target.

5. **Prompt Injection Protection** -- All tool inputs are passed as typed Python parameters (`str`, `int`, `bool`), never interpolated into shell commands. No `eval`, `exec`, or subprocess calls with user-controlled data.

6. **Least Privilege** -- 14/23 tools are read-only. `create_namespace`, `create_tkc_cluster`, `delete_namespace` and `delete_tkc_cluster` preview by default: without `confirm=True` they change nothing and return the blast radius, and `confirm=True` is refused while a blocker stands or part of the blast radius could not be read. The running-workload guard on `delete_tkc_cluster` can only be skipped with `force=True`. All write operations are audit-logged to `~/.vmware/audit.db` (SQLite WAL, via vmware-policy).

## Supported AI Platforms

| Platform | Status |
|----------|--------|
| Claude Code | Native Skill |
| Goose (Block) | MCP via stdio |
| Cursor | MCP mode |
| Continue | MCP mode |
| VS Code Copilot | MCP mode |
| Python CLI | Standalone |
