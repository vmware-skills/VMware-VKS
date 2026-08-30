## v1.8.12 — a Workload Management failure was reported as a connectivity failure

`vmware-vks check` against a real vCenter printed the same row twice: passing
with the vCenter version, then failing with a 401 from the namespace-management
endpoint. One `try` wrapped connect, version and the WCP query, and the single
`except` labelled every failure "vCenter reachable" — while the row above proved
vCenter was reachable. What failed was WCP.

A diagnostic that names the wrong layer sends the operator to check networking
and credentials that are already fine. Failures are now attributed to the stage
that raised them.

Found running against a live vCenter 8.0.3 with no Supervisor enabled.

## v1.8.11 — two wrong numbers: the server's own version, and the advertised tool count

Both defects were invisible to the test suites and both were user-facing.

- **The MCP server reported the SDK's version as its own.** `FastMCP` accepts no
  `version` argument and leaves the lowlevel server's at `None`; with it `None`
  the SDK answers `initialize` with its OWN version. Every skill in the family
  therefore told its client it was mcp 1.29.1 — a number that exists for no
  package here, and one that would change with an SDK bump and no code change of
  ours. Verified end to end rather than by reading: unset the field and a probe
  server reports the installed SDK's version; set it and it reports ours.

Also new: this repo is installable as a Claude Code plugin
(`/plugin install vmware-vks@vmware-skills`). The skill and its MCP server arrive in
one step; nothing is duplicated, the manifest points at the existing `skills/`
tree. family_smoke gained three gates — the server's reported version, the plugin
manifest's agreement with pyproject, and the advertised tool count against the
live registration.

## v1.8.10 — VM Service reads (VCF 9.1 vm-operator CRDs): snapshots, VM groups, multi-NIC readout

### Added: three read-only VM Service tools (20 → 23 MCP tools)

Supervisor **VM Service** objects are now readable through the same Layer-2 path
as the TKC tools — the Supervisor Kubernetes API via the kubernetes client
(`CustomObjectsApi`), reading `vmoperator.vmware.com` CRDs. **Not** vCenter REST,
**not** pyVmomi.

| tool | reads | envelope adds |
|---|---|---|
| `list_vm_snapshots` | `VirtualMachineSnapshot` in a Namespace | `served_version` |
| `list_vm_groups` | `VirtualMachineGroup` + `spec.bootOrder` | `served_version` |
| `list_vm_network_interfaces` | one VM's `spec.network.interfaces[]` (multi-NIC) | `vm_name`, `served_version` |

All three are `[READ]`, `risk_level="low"`, return the family list envelope, and
have matching CLI subcommands (`vmware-vks vm snapshots|groups|nics`). SKILL.md,
the tools table, and the header count are updated to **23 MCP tools (16 read, 7
write)** — verified against `mcp.list_tools()` (踩坑 #34: declared count must
equal the exposed count).

The served CRD version is **discovered at runtime** through the K8s discovery API
(`ApisApi.get_api_versions()`), newest served among v1alpha4/5/6 preferred —
never hardcoded. When a kind's CRD is not served (e.g. `VirtualMachineSnapshot`
needs vmoperator v1alpha5+, `VirtualMachineGroup` needs v1alpha4+), the tool
returns a teaching error that names the required version instead of a traceback.

### Fixed — Fable5-review hardening applied during this build

- **Anti-phantom-endpoint guard (踩坑 #36).** The group, plurals, kinds and
  minimum versions are pinned in `tests/eval/spec/vmservice_endpoints.py`; a
  regression test uses AST + literal scanning to assert the ops module can never
  reach a `group=`/`plural=` outside that pinned set — so the CRD surface cannot
  silently drift into an invented path (the failure mode a prior skill shipped,
  where half the hallucinated REST paths 404'd).
- **Empty is an explicit answer, not "no problem" (踩坑 形态 #1).** Every
  collection is walked to exhaustion via the `continue` token, so `total` is a
  real count and `truncated` is always `False` rather than inferred from a page.
  A missing field degrades to `""` / `None` / `[]` (never a crash), and a VM
  with no `spec.network` returns an empty NIC list instead of an error.
- **Non-dict members/stages/NICs are skipped** rather than trusted, and each
  `api_client` is closed in a `finally`.
- Removed a dead `import json` in `kubeconfig_get` left over from an earlier edit
  (surgical cleanup, no behavior change).

### Beta / real-hardware caveats (read before trusting field-level output)

This is a **beta** surface. The CRD **paths** — group `vmoperator.vmware.com`,
plurals `virtualmachines` / `virtualmachinesnapshots` / `virtualmachinegroups`,
and the per-kind minimum versions — are pinned to the **VCF 9.1 verified-endpoints
spec (section D)** and cross-checked by regression tests, but they have **not been
exercised against a live VCF 9.1 Supervisor**. Verification is at the CRD-surface
/ path level only.

- **Response FIELD names are not confirmed on a live 9.1 appliance.** The fields
  read — `spec.vmName`, `spec.network.interfaces[].network.{name,kind,apiVersion}`,
  `spec.bootOrder[].members[].{kind,name}` / `powerOnDelay`,
  `status.conditions[].Ready` — are how vm-operator has shaped these objects, read
  defensively so an unexpected/renamed field degrades to empty rather than
  crashing. Treat field-level output as provisional until real-hardware
  verification (same posture as the still-pending `/wcp/login` real-machine check).
- **INFERRED bits, called out explicitly:** the snapshot `vmName` vs
  `virtualMachineName` spelling is a guessed-either-way guard (vm-operator has
  used both across versions); the per-kind minimum-version gate (Snapshot
  v1alpha5+, Group v1alpha4+) is a **spec claim**, not a live check — the actual
  served version is runtime-discovered so the tool adapts, but the minimum gate
  itself is inferred from the spec.
- **No PromQL / PAIS / metrics-collector paths are involved here.** Those
  INFERRED / collector-pending surfaces live in other skills' specs; this change
  touches only the vmoperator CRD read path, and nothing in it is
  collector-pending.

No new dependencies; read-only, so no policy/audit posture change beyond the
existing `@vmware_tool` wrapping.

## v1.8.9 — moved to vmware-skills org + MCP Registry namespace io.github.vmware-skills/vmware-vks

Repo transferred from github.com/zw008 to github.com/vmware-skills (redirects preserve old links).
MCP Registry server renamed to `io.github.vmware-skills/*`; the old `io.github.zw008/*` entry is deprecated.
All in-repo links updated. No functional code change on this line beyond the org move.

## v1.8.8 — CLI writes now route through policy + audit, exactly like the MCP tools

Every state-changing CLI command is now wrapped by `@guarded`, the CLI counterpart
to the MCP `@vmware_tool` decorator: it runs the same vmware-policy `guard()`
authorization and writes the same `audit_call()` row to `~/.vmware/audit.db`. A
`delete`/`disable`/destructive command run through a shell is now authorized and
recorded exactly like the equivalent MCP tool — closing the gap where CLI writes
bypassed policy and landed only in the legacy per-skill log (HLD I-1/I-8).

- a policy `deny` rule now refuses the operation on the CLI with a teaching line
  naming the rule that fired, not a traceback
- the legacy per-skill audit log is still written this release (dual-write); it is
  removed at 2.0
- **requires vmware-policy >= 1.8.8** (the release that adds the shared `guarded` core)
- a regression test derives the write-command set from the MCP `[WRITE]` markers and
  asserts every one is `@guarded`, so a new write command cannot ship unguarded

Also carries the environment-field docstring correction (an optional label a `deny`
rule may scope to — there is no "warn now / refuse next major" gate).

## v1.8.7 (2026-07-21) — the skill-level read-only switch is removed; read/write authorization is the vCenter account's job (RBAC)

### Removed: `VMWARE_READ_ONLY` / `read_only:` — give the agent a read-only service account instead

The skill-level read-only switch is gone. It was enforced only on the MCP tool
registry, and any agent with a shell (every SKILL.md grants `allowed-tools: Bash`)
could reach the same change one CLI command away — so it withheld the *tool*, not
the *capability*. It was never a real boundary.

To run an agent read-only, give it a **read-only vCenter/NSX service account
(RBAC)**. Writes are then refused at the platform, un-bypassably, regardless of
surface or shell — the one place read/write control cannot be stepped around. A
config still carrying `read_only: true` is ignored, with a one-time warning that
names the replacement (no silent behavior change).

### Removed: approval tiers and the declared-environment gate (via vmware-policy)

The graduated-autonomy approval tiers (`confirm`/`dual`/`review`) and the "declare
an environment or be refused" baseline are removed — they only ever fired on the
rarest configuration while carrying the family's most complex machinery. Opt-in
`deny` rules and the maintenance window remain, and apply identically wherever a
tool runs.

### Added: offline / air-gapped install docs

The README now covers installing from source without editable mode (for older
`pip`) and building wheels to carry onto an air-gapped host — the modern PEP 517
layout has no `setup.py` by design, which is expected, not a missing file.

This release also carries the accumulated fixes staged since 1.8.5.

## v1.8.6 (unreleased) — `clusters`, `pvcs` and `pvc_count` come back as deprecated aliases

### Fixed — two v1.8.0 renames that broke callers without telling them

v1.8.0 said every `[READ]` list tool now returns the family envelope "instead of
a bare array". For two tools in this skill that was not true — both already
returned a keyed dict, so the conversion renamed a key:

| tool | v1.7.7 | v1.8.0 |
|---|---|---|
| `list_tkc_clusters` | `{total, clusters}` | `clusters` → `items` |
| `list_namespace_storage_usage` | `{namespace, pvc_count, pvcs}` | `pvcs` → `items`, `pvc_count` **deleted** |

The distinction matters because it decides whether you find out. Replacing a
bare array with a dict breaks loudly — `result[0]` raises. Renaming a key inside
a dict that was already there breaks quietly: `result.get("clusters", [])` kept
returning a value, just always `[]` — which reads as "this namespace has no
clusters", not as a failure.

The v1.8.0 notes made this worse rather than surfacing it. A reader who used
either tool, checked the sentence, saw that their payload was a keyed dict and
not a bare array, would correctly conclude the described change was not
theirs — and ship the bug. The v1.8.0 entry below now carries a correction
saying so explicitly.

Worth stating plainly: **this repo already knew.**
`tests/eval/regression/test_result_envelope.py` has recorded both old shapes in
its module docstring since 2026-07-20. The knowledge was in the test suite and
never reached the release notes.

**`clusters` and `pvcs` are restored as deprecated aliases**, pointing at the
*same list object* as `items` rather than copies, so they cannot drift.
**`pvc_count` is restored** as well — it was deleted outright rather than
renamed — and is derived from `returned`, so it cannot disagree with the list it
counts. `items` remains the primary key. **All three are removed in 2.0** —
migrate to `items` / `returned`.

Pinned by `tests/eval/regression/test_deprecated_key_aliases.py`, which asserts
a pre-v1.8.0 caller still sees its rows, that each alias is the same object
(verified by mutating one and reading the other), and that `pvc_count` always
equals `len(pvcs)`.

Two sibling skills shipped the same shape and are fixed in the same release:
`list_virtual_machines` in vmware-monitor and `list_vm_tags` in
vmware-nsx-security.

---

## v1.8.5 (2026-07-20) — the two fixes v1.8.4 announced now actually work

Four adversarial reviews of v1.8.4 found that both of its headline fixes were
incomplete in ways the release notes did not reflect. This release makes them
real. If you are on 1.8.4, this is the one to take.

### Fixed — a failure that was *returned* was still audited as a success

vmware-policy 1.8.4 added `report_tool_failure()` for tools that catch an
exception and return an error payload instead of raising. **No skill called it.**

Every string-returning tool therefore kept doing exactly what 1.8.4 said it had
stopped doing: writing `status=ok` to `~/.vmware/audit.db` for an operation that
failed, recording an undo token for a change that never happened, and telling the
circuit breaker the call succeeded so repeated failures never tripped it.

The surface this covered is not marginal:

| Skill | What was mis-audited |
|---|---|
| vmware-aiops | 25 of 49 tools, including **every undo-bearing write** — a failed `vm_power_on` left an undo token saying "power it back off" |
| vmware-avi | all 28 tools, including `vs_toggle` and `ako_restart` |
| vmware-storage | all 4 write tools |
| vmware-nsx | the 5 delete tools |

vmware-avi is worth calling out: before 1.8.4 its exceptions propagated and the
audit was correct. 1.8.4 caught them and returned a string, so **that release made
its audit trail worse than it had been.**

Skills whose tools already return dict payloads (vmware-monitor, vmware-vks,
vmware-aria, vmware-log-insight, vmware-harden, vmware-debug, vmware-pilot) were
already detected correctly. They gained a test proving it rather than a redundant
call.

### Fixed — narrowing `OSError` did not close the leak it was meant to close

1.8.4 narrowed the `_safe_error` passthrough because bare `OSError` let TLS and
DNS failures reach the agent with hostnames and certificate subjects in them.
That narrowing had no effect on the error it was written for:

```
ssl.SSLCertVerificationError → ssl.SSLError → OSError, ValueError
```

`ValueError` has been on every allowlist since long before 1.8.4, so a
certificate failure kept passing through — the commonest self-signed-certificate
failure in this family, carrying the hostname it was checked against. An
allowlist structurally cannot express "not this one".

Where `ssl.SSLError` can actually surface — the pyVmomi skills — it is now
reduced *ahead* of the allowlist. In the httpx skills TLS arrives wrapped as
`httpx.ConnectError`, and in vmware-avi as `requests.exceptions.SSLError`, so the
guard cannot fire there; in those skills the leak was the raw exception
interpolated into an already-allowlisted `*ApiError`, and that is now authored
text naming the config target and `verify_ssl` instead of the exception.

The missing-password error — this family's most common first-run failure, whose
entire remedy is the environment variable name it carries — keeps its message
through a narrow `ConfigError(OSError)` rather than the base class. Connection
failures are translated at the connection layer into an authored remedy that
names the target and the setting to change, with the raw detail left on
`__cause__` for the server log.

### Also fixed

- **vmware-vks**: the quickstart documented a password variable the code never
  reads — following `README.md` verbatim produced "Password not found". Five
  places, plus six references to a `doctor` command this CLI has never had, two
  descriptions promising fields the tools do not return, and eight teaching
  messages that `RuntimeError` was masking.
- **vmware-nsx**: an error cited `--route-advertisement`; the flag is `--advertise`.
- **vmware-pilot**: `get_workflow_status` told the model to call `approve` — a
  tool the read-only gate withholds — as the required next step; and a hint
  pointed at a filename that could never appear in that message.
- **vmware-aiops**: `vm_task_status` polling a *failed task* returned
  `{"state": "error", "error": ...}` from a successful read, which the new
  detection read as the call itself failing. The field is now `task_error`.
  **This is a breaking change for anything parsing that payload.**
- Several remedies that were still being cut by the 300-character cap the 1.8.4
  notes claimed to have addressed.

### Known and not fixed

`ConnectionError` remains one type from two sources in several skills — a
skill's own authored message and urllib3's `HTTPSConnectionPool(host=..., port=...)`
share it, and an allowlist cannot separate them. vmware-vks is converted; the
rest need their own domain type and are deferred rather than half-done.

## v1.8.4 (2026-07-20) — errors that teach, and tool descriptions a small model can route from

A capability eval was rolled out across the family and asked two open questions:
when a call fails, is the model told enough to fix it, and can it pick the right
tool from the description alone? Both answers were worse than anyone thought, and
in several places the reason was that the measurement was looking somewhere other
than where the model reads.

### Fixed — teaching messages were being discarded on the way to the agent

`_safe_error` reduces unrecognised exceptions to `"<Class>: operation failed."`
so raw API text, credentials in URLs and internal paths cannot reach an agent.
Its allowlist held only the builtin validation errors — so this skill's **own**
domain exceptions, the ones that exist precisely to carry a corrected next step,
had their messages replaced by their class names.

The effect was invisible from the CLI, which prints those messages in full.

The worst case was shared by nine skills: `config.py` raises exactly one
`OSError`, the missing-password error, whose entire remedy is the environment
variable name it names. An agent hitting an unconfigured target received
`OSError: operation failed.` and had nothing to act on. That is the family's most
common first-run failure, and it landed one release after the documented variable
names were corrected — so the message that would have unstuck the operator was
the one being thrown away.

The rule is now the property it always meant: **every exception this skill raises
on purpose passes through**, and only genuinely unplanned ones are reduced.
`RuntimeError` stays reduced — it is the generic catch-all and in several skills
carries raw upstream text.

### Fixed — error messages now carry the correction

Every message that reported a failure without saying how to recover was
rewritten: it names the offending value, gives an imperative remedy, and names
something concrete to act on — a tool that exists, a real CLI command, a config
file, an environment variable. Recovery becomes an instruction-following problem
rather than an inference one, which is what a weak model can still do.

Three classes of defect surfaced while doing it:

- **Remedies that were never delivered.** `_safe_error` truncates with no
  ellipsis, so a message longer than the cap loses its closing sentence
  silently. One message had been shipping at 396 characters against a 300-char
  cap — its remedy had never once reached an agent. Messages now lead with the
  remedy so a long interpolated value truncates the expendable detail instead.
- **Commands that do not exist.** One skill's error hints named a `doctor`
  subcommand it does not have.
- **Tools that do not exist.** A tool description pointed at two sibling-skill
  tools that had been renamed, and another named a tool that had moved to a
  different skill entirely.

### Improved — tool descriptions state when to use them and what to call next

The description is the API for a small model: an unstated routing rule is a
routing rule that does not exist, and a tool with no stated next hop is one the
model stops at. Descriptions now say when to prefer this tool over a sibling,
what shape comes back, the caveat that bites, and which tool to call after.

**Manifest size did not grow.** Descriptions load into every session, so the
routing clauses were paid for by cutting duplicated reference material —
repeated boilerplate, examples that restated the parameter list, and prose
copies of the pagination contract.

### Note

Every tool and CLI command named anywhere in this release was verified against
the live MCP registry and the live command tree, not against documentation.

## v1.8.3 (2026-07-20) — credentials resolve as a pair; documented env vars now exist

### Added — the per-target username can come from the environment

Adapted from [VMware-AIops#33](https://github.com/vmware-skills/VMware-AIops/pull/33) by
@wright-bench, with thanks. The password already resolved from an env var; the
username did not, so a deployment injecting credentials from a secret store
(systemd `EnvironmentFile`, container secrets, a vault sidecar) could externalise
only half of the pair — and a config-file username paired with an env password
from a different account logs in as nobody.

`<PASSWORD-KEY-PREFIX>_USERNAME` now overrides the `username:` in config.yaml,
using that skill's own password-key convention. Absent, config.yaml still wins;
nothing changes for anyone not setting it.

**Resolved on every access, like the password.** The contributed version read the
username once at load time while the password stayed a property, which
reintroduces exactly the split the override exists to prevent: a sidecar rotating
both halves mid-process moves the password and leaves the username behind. A test
pins that both halves resolve at the same moment.

### Fixed — documented credential variables that the code never read

Rolling the above across the family surfaced a separate defect: four skills
documented a password variable their own loader does not look up. An operator
following the documentation exactly — correct file, correct place, correct-looking
name — got "Password not found".

| Skill | Documented | Actually read |
|---|---|---|
| vmware-nsx | `VMWARE_NSX_<TARGET>_PASSWORD` for target `nsx-prod` → `VMWARE_NSX_PROD_PASSWORD` | `VMWARE_NSX_NSX_PROD_PASSWORD` |
| vmware-nsx-security | `VMWARE_<TARGET>_PASSWORD` | `VMWARE_NSX_SECURITY_<TARGET>_PASSWORD` |
| vmware-aria | `VMWARE_<TARGET>_PASSWORD` | `VMWARE_ARIA_<TARGET>_PASSWORD` |
| vmware-vks | `VMWARE_<TARGET>_PASSWORD` | `VMWARE_VKS_<TARGET>_PASSWORD` |
| vmware-avi | three different forms across three files | `<CONTROLLER>_PASSWORD` |

The prefixes genuinely differ per skill, so nothing could be fixed by
standardising a pattern — each repo's docs were corrected against its own code.
The code was left alone: changing a key would break every existing deployment.

`family_smoke.sh` now compares the credential variables named in each repo's docs
against the ones that repo's code builds, so the two cannot drift apart again.

## v1.8.2 (2026-07-20) — the MCP server moves into the package namespace

### Fixed — co-installing two skills broke all but the last one

Every skill shipped its MCP server as a **top-level `mcp_server` package**. Python
has one top-level namespace, so installing any two of them into one environment let
the second overwrite the first — silently, with no error and no warning.

    uv tool install vmware-aiops   ->  49 tools   (correct)
    uv pip  install vmware-aiops   ->  27 tools   (Monitor's read-only server)

vmware-aiops depends on vmware-monitor, so this was not an edge case: **every pip
install hit it**, and the operator got 27 read-only tools where 49 were expected,
with all 35 write tools missing. Docker images, shared MCP hosts and CI runners that
install more than one skill were affected the same way.

The server now lives at `vmware_<skill>/mcp_server/`, a name only this package can
claim. Introduced 2026-02-26; it survived 70 releases because every test ran against
a single package in its own repo, where the local directory shadows site-packages —
the conflict was invisible by construction.

**Migration.** Console scripts are unchanged: `vmware-<skill>` and
`vmware-<skill>-mcp` work exactly as before, as does `"command": "vmware-<skill>",
"args": ["mcp"]` in an MCP client config. Only a direct `python -m mcp_server`
breaks; use `python -m vmware_<skill>.mcp_server`.

### Added — `references/agent-guardrails.md` in every skill

The operating rules for local and small models (Llama 3.3 70B, Qwen, Mistral via
Goose / Ollama / OpenShift AI) existed in two skills. They now ship in all 13, each
with its own tool counts and failure modes, and are linked from every SKILL.md.

### Added — `doctor` checks `.env` permissions

Every other skill's doctor checked this; vmware-vks imported `ENV_FILE` and never
used it — the fingerprint of a check planned and dropped. The file holds per-target
passwords and CLAUDE.md requires it be `chmod 600`.

## v1.8.1 (2026-07-19) — read-only mode reaches the surfaces that teach it

v1.8.0 put read-only mode in the code and documented it in the README only.
Every other layer was empty, and each serves a different reader: SKILL.md is what
the agent loads, setup-guide is what an operator reads while configuring, `doctor`
is where they verify it took. The gap had two concrete costs.

An agent read SKILL.md, called a write tool the gate had withheld, and got nothing
back — with no way to learn that the absence was a deliberate lockdown rather than
a fault. It reads as a broken tool, so the model retries or hunts for a workaround.

An operator who set the switch had no way to confirm it. The only signal was a line
in the MCP server's start-up log.

### Added — the feature is now documented where each reader looks

- **SKILL.md** — a short section telling the agent that a missing write tool is a
  lockdown, not a fault: name the blocked operation, do not retry, do not route
  around it.
- **references/setup-guide.md** — the operator's view: how to enable it, the
  precedence chain, and how to verify.
- **references/capabilities.md** — which tools the gate withholds.

### Added — the doctor reports the read-only state

`vmware-vks check` (which runs the doctor) now shows whether read-only mode is on, **which** of the three
switches decided it, and the value as written. A typo'd value (`ture`) is called
out as a typo rather than reported as a confident ON — it resolves to on, which is
fail-closed but almost never what was meant.

The resolution runs through `vmware_policy.read_only_status()` rather than a local
copy of the precedence chain: a doctor that disagrees with the gate it reports on is
worse than no doctor. Requires `vmware-policy>=1.8.1`.

## v1.8.0 (2026-07-18) — read-only mode, working policy defaults, declared environments

Family release driven by [VMware-AIops#31](https://github.com/vmware-skills/VMware-AIops/issues/31),
where an operator running Llama 3.3 70B (Goose / OpenShift AI, on-prem H100) had to
hand-write 17 prompt guardrails to make tool calling reliable. A prompt is advisory — a
model can ignore it. Every guardrail that could move into the harness has.

### Added
- **Read-only mode.** Set `VMWARE_READ_ONLY=true` (or `VMWARE_<SKILL>_READ_ONLY`, or
  `read_only: true` in config.yaml) and every write tool is removed from the MCP registry
  at start-up. `list_tools()` never offers them, so the model cannot call what it cannot
  see. **Off by default** — nothing changes unless you turn it on. Fail-closed: if the
  mode is requested but cannot be guaranteed, the server refuses to start rather than
  running open.
- **`environment:` on each config target**, declaring which environment it is
  (production / staging / lab). Policy rules scope by this value.

### Added — list results now state whether they are complete

Every `[READ]` list tool returns the family envelope instead of a bare array:

    {"items": [...], "returned": 50, "limit": 50, "total": 213,
     "truncated": true, "hint": "Showing 50 of 213. Raise limit or narrow the query..."}

This closes the reported failure where long responses were summarised as "no data
returned": a bare list gives a model no way to tell a complete answer from page one, so
it guessed. `truncated: false` now positively states completeness — including when
`items` is empty, which means "checked, found none", not "the call failed".

> **Correction (v1.8.6).** The sentence "instead of a bare array" above is true
> of 51 of the 55 tools converted family-wide. It is false of four, and **two of
> them are in this repo**.
>
> Neither was a bare array before v1.8.0. Both returned a keyed dict, so the
> conversion renamed a key rather than changing the payload type:
>
> | tool | v1.7.7 | v1.8.0 |
> |---|---|---|
> | `list_tkc_clusters` | `{total, clusters}` | `clusters` → `items` |
> | `list_namespace_storage_usage` | `{namespace, pvc_count, pvcs}` | `pvcs` → `items`, `pvc_count` **deleted** |
>
> That is a different kind of break from the other 51. A bare-array caller doing
> `result[0]` gets an immediate `KeyError` on a dict and finds out at once; a
> caller doing `result.get("clusters", [])` kept running and silently saw zero
> clusters, which reads as "this namespace is empty" rather than as a failure.
>
> If you read this section when v1.8.0 shipped, checked either tool, saw a keyed
> dict and concluded the change did not apply to you: it did. The count in the
> bullet below is the tell — three tools are listed as converted, but five ops
> functions return the envelope. The two missing from the count are exactly the
> two above, and they were left out of the prose for the same reason.
>
> **v1.8.6 restores `clusters` and `pvcs`** as deprecated aliases pointing at the
> *same list object* as `items` (not copies — copies drift), and restores
> `pvc_count`, derived from `returned` so it cannot disagree with the list it
> counts. All three are removed in 2.0. Migrate to `items` / `returned`.
>
> The other two, for anyone running more of the family: `list_virtual_machines`
> (`vms` → `items`) in vmware-monitor, and `list_vm_tags` (`tags` → `items`) in
> vmware-nsx-security.

- **3 tool(s) converted** across ops, MCP and CLI. All three report a real `total` and `truncated: false` — the REST call is a single
  un-paged GET, so "this is complete" is stated rather than left to inference.

### Changed — migration, read this
- **Approval tiers now actually run.** They shipped in v1.6.0 but the engine only ever
  read `~/.vmware/rules.yaml`, and a fresh install has no such file — so every deny rule,
  maintenance window and approval tier had been inert on every install that never
  hand-authored one. A packaged baseline now loads when you have written no rules of your
  own. Writes at medium risk and above are stamped with their tier in the audit log;
  irreversible work and guest execution against a target declared `production` require a
  named approver via `VMWARE_AUDIT_APPROVED_BY`.
- **`environment:` will become required for writes.** Today a state-changing operation
  against a target that declares none still runs and logs a warning. **The next major
  release refuses it.** Declare it now and that upgrade is a no-op:

      targets:
        prod-vc01:
          host: vc01.corp.local
          environment: production

  Read-only operations are never affected, in this release or the next. Check what applies
  to your targets before upgrading: `vmware-audit policy --operation vm_delete --env <env>`.

### Fixed
- **Policy glob patterns with a leading wildcard silently matched nothing.** A rule written
  `operations: ["*_delete"]` parsed fine, read correctly, and never fired — only a trailing
  `*` was honoured. Now full glob matching, for operations and environments alike.
- Config-path overrides (`VMWARE_<SKILL>_CONFIG`) are honoured when reading `read_only`
  and `environment`, so a setting in a custom config file is no longer silently ignored.

### Notes
- Requires `vmware-policy>=1.8.0`; publish that package first.
- `vmware-audit policy` reports which rules are in force and where they came from —
  including the case where your rules file exists but failed to parse, which previously
  looked identical to "policy is working".

## v1.7.7 (2026-07-17) — session-probe None-shape fix + mcp 1.28.1

Family fix pack — no new tools, no schema changes.

### Fixed
- **A dead cached session could be returned as live** (family fix, external
  fork report VMware-AIops PR #32). An expired token can make
  `sessionManager.currentSession` return `None` without raising, and the
  raise-only liveness probe treated that dead session as alive. The probe now
  checks `currentSession is not None`; the exception path (already correct
  here) is unified on the family-standard bare `except Exception`. Three
  regression tests pin the probe shapes (raise → evict + reconnect, None →
  evict + reconnect, live → cache reuse).

### Security
- Lockfile bumps `mcp` to **1.28.1**, clearing three GHSA HIGH advisories
  against the MCP Python SDK (WebSocket Host/Origin validation, HTTP
  transport principal verification, experimental task-handler cross-client
  access). stdio-only servers are not directly exposed, and installs resolve
  `mcp` fresh from PyPI — this mainly matters for from-source checkouts.

## v1.7.5 (2026-07-13) — family version alignment (no code change)

Version-alignment release only; no functional change since v1.7.4.

## v1.7.4 (2026-07-13) — family version alignment

## v1.7.3 (2026-07-03) — family version alignment

## v1.7.2 (2026-07-02) — list hardening

### Fixed
- **Graceful listing at scale.** Harbor registry info now degrades gracefully
  (a warning, not an aborted listing) if a per-registry detail call fails, and
  TKC cluster / workload listings page via `limit` + continuation tokens so very
  large fleets don't land in one response. Output shape unchanged.

## v1.7.1 (2026-07-02) — family version alignment

No code changes. Version bump to stay aligned with the v1.7.1 family release
(VMware-AIops + VMware-Monitor large-inventory scale fix — PropertyCollector
batching to stop per-object lazy SOAP round-trips, GitHub issue #31).

## v1.7.0 (2026-06-27) — guided onboarding + teaching auth errors

### Added
- **`vmware-vks init` — interactive first-run setup wizard.** Prompts for host /
  username / password and writes `config.yaml` + `.env` for you. The password is
  stored grep-safe (`b64:`, never plaintext on disk) and `.env` is locked to
  0600, then the connection is verified. Replaces the manual "mkdir + cp
  config.example.yaml + edit YAML + chmod 600" dance.

### Changed
- `doctor` now points to `vmware-vks init` when config/credentials are missing
  (previously suggested a command that did not exist), keeping the manual steps
  as a fallback.
- Authentication and TLS failures now print a teaching message naming the exact
  file and env var to fix (`~/.vmware-vks/.env` password var, `config.yaml`
  username) plus a `verify_ssl: false` hint for self-signed labs.
- Teaching now covers all three auth paths: vCenter login (pyVmomi
  `InvalidLogin`), Supervisor `/wcp/login` (HTTP 401/403), and TLS.

## v1.6.1 (2026-06-24)

### Added
- **`.env` passwords are auto-obfuscated to a grep-safe `b64:` form** on first
  load and decoded transparently at runtime — plaintext no longer sits in
  `~/.<skill>/.env` for a casual `grep` to find. Values are read/written through
  python-dotenv's own parser, so the stored secret never drifts from the
  configured one (handles quotes, inline comments, trailing whitespace, and a
  password that literally starts with `b64:`). **Obfuscation, not encryption** —
  for real at-rest secrecy, inject the password from a secret manager instead of
  storing `.env`. New regression suite (10 cases) covers dotenv parity, the
  `b64:`-prefixed edge case, idempotency, and 0600 preservation.

## v1.6.0 (2026-06-22) — trust architecture: undo tokens

### Added
- **Undo-token recording** (vmware-policy 1.6.0): `create_namespace`→`delete_namespace`,
  `create_tkc_cluster`→`delete_tkc_cluster`.
- Inherits harness budget guard, audit accountability fields, and graduated risk tiers.

### Changed
- Requires **vmware-policy >= 1.6.0**.

## v1.5.39 (2026-06-22) — family version alignment

No code changes. Version bump to stay aligned with the v1.5.39 family release
(AIops snapshot-delete async + honest-timeout token-burn fix; Storage datastore-browse timeout fix).

## v1.5.38 (2026-06-12) — backlog finish: one-command Supervisor auth preflight

### Added
- **`vmware-vks preflight-auth [--target <name>]`** — runs the real `POST /wcp/login` flow against the
  configured Supervisor and reports, per target: vCenter reachable → `/wcp/login` HTTP status →
  parseable `session_id` → does the JWT authenticate a trivial Supervisor K8s API call. Makes the
  v1.5.36 auth rewrite validatable in one command (it never tracebacks; failures are teaching status). (#13)
- Tightened wcp_login unit tests (exact endpoint/field/verify_ssl pins) so future Supervisor-version
  drift is caught.

## v1.5.37 (2026-06-12) — backlog: fewer Supervisor round-trips

### Fixed
- TKC operations resolve the Supervisor endpoint once (cached per host) instead of re-running two vCenter
  REST calls per Kubernetes-client build. (#12)

## v1.5.36 (2026-06-12) — correct Supervisor authentication + safety fixes

### Fixed
- **Supervisor/TKC authentication rewritten** — the Kubernetes bearer token was the pyVmomi SOAP
  session key, which a real Supervisor rejects; it now comes from `POST /wcp/login` (the JWT that
  `kubectl vsphere login` uses), cached per host/user with TTL + 401 invalidation.
  *(This auth path should be smoke-tested against a live Supervisor before relying on it.)*
- **`scale_tkc_cluster` no longer wipes the worker pool spec** — the merge-patch replaced the whole
  machineDeployments list (dropping `class: node-pool`); it now reads, edits the matched pool, and
  patches the full preserved list.
- **Namespace-delete TKC guard fails closed** — it previously deleted a namespace with clusters
  inside if the Kubernetes API was unreachable.
- **Stale session metadata is evicted immediately** (fixing an `id(si)`-reuse hazard) and a latent
  `vmodl.fault.NotAuthenticated` AttributeError on eviction was corrected.
- `dry_run` previews no longer require `confirmed=True`; topology access is null-guarded; CLI writes
  are audited; `tkc delete --force` renamed to `--skip-workload-check` so `--force` means one thing.

### Added
- Centralized `VksApiError` translation across the REST and Kubernetes paths (teaching hints,
  GET-only transient retry).

## v1.5.35 (2026-06-10) — security fix: kubeconfig TLS bypass + token-file hardening

### Fixed
- **Generated kubeconfigs now honour `verify_ssl`** instead of hardcoding
  `insecure-skip-tls-verify: true`. Previously the kubernetes client never validated the
  Supervisor/TKC API certificate — even in production. It now validates against the system
  CA bundle unless `verify_ssl: false` is explicitly set for a lab.
- **Kubeconfig file writes** (which carry a live session token) refuse to follow a symlink
  and are created with `O_NOFOLLOW` + mode 0600.
- **MCP tools route errors through `_safe_error()`**; audit dir 0700 / log 0600.
- **Docs corrected** to reflect that auditing writes both `~/.vmware/audit.db` (SQLite, via
  vmware-policy) and a local JSON-Lines mirror at `~/.vmware-vks/audit.log`.

This release aligns the whole family back to a single version (1.5.35); vmware-policy and vmware-pilot return to the shared number after sitting at 1.5.22.

## v1.5.32 (2026-06-08) — Invented Supervisor REST endpoint + wire-field fixes

### Fixed
- `list_supervisor_storage_policies`: calls the real
  `GET /api/vcenter/storage/policies` (the previously used
  `namespace-management/storage/storage-policies` path never existed — 404 on
  every call). Returns policy ID, name, description.
- VM classes: memory from the `memory_MB` wire field; `gpu_count` derived from
  `devices` (vGPU + dynamic DirectPath I/O) — the old flat fields don't exist.
- Harbor: Summary fields parsed correctly (`registry`/`ui_access_url`/
  `cluster`/`version`) + per-registry enrichment for storage/health.
- Supervisor status: Kubernetes version from
  `GET /namespace-management/software/clusters/{cluster}` → `current_version`
  (not a Clusters.Info field), with graceful degradation.

### Tests & docs
- +9 shape regression tests; vim-attribute conformance regression added;
  safety test asserts CLI confirm guards; docs synced (including removal of a
  false "admin credentials" claim from the Harbor tool description).

## v1.5.30 (2026-06-07) — Tool description quality (Glama TDQS)

### Improved
- Rewrote MCP tool descriptions flagged by Glama's Tool Description Quality Score review:
  per-parameter semantics (format, defaults, valid values), return-field documentation,
  sibling-tool routing guidance, and behavioral transparency (side effects, audit logging,
  async semantics). Corrected descriptions that overstated or misstated actual behavior.
- No functional changes; descriptions only.

## v1.5.29 (2026-05-29) — VCF 9 Verification Status + TKC API Auto-Detect Docs

### Documentation
- README.md / README-CN.md: VCF 9.0/9.1 row in Version Compatibility table strengthened with v1.5.23 RELEASE_NOTES exact framing ("Workload Management API surface not yet verified by maintainers"). Architecture diagram updated to show API auto-detection + in-memory kubeconfig.
- SKILL.md: TKC API version auto-detection note in "Deploy a New TKC Cluster" pre-flight section (preference order: `cluster.x-k8s.io/v1` → `v1beta1` with optional override); in-memory kubeconfig paragraph in Audit & Safety.
- capabilities.md: new "TKC API Version Auto-Detection (v1.5.18+)" subsection explaining `_resolve_tkc_version()`, per-host caching, fallback behavior; "In-Memory Kubeconfig" row in Safety Features table.
- setup-guide.md: VCF 9 verification caveat; in-memory kubeconfig in Security elements.
- README files: Python 3.10+ prerequisite (v1.5.27).

### No code changes
Documentation-only release. No version bump for the underlying TKC auto-detect or kubeconfig features (those shipped in v1.5.18).

## v1.5.28 (2026-05-20)

**Fix `subclass() arg 1 must be a class` in goose/old mcp environments** —
v1.5.25–1.5.27 replaced `X | None` with `Optional[X]` but kept
`from __future__ import annotations` at the top of `mcp_server/server.py`.
Under mcp 1.10–1.13 (which Goose and some sandboxes pin), `Tool.from_function`
calls `issubclass(param.annotation, Context)` without resolving forward refs,
so string annotations crash the entire server load. Removed
`from __future__ import annotations` from `mcp_server/server.py` so annotations
are real classes; verified all tools load under mcp 1.10 and 1.14.

Traceback location: `mcp/server/fastmcp/tools/base.py:67`. CLAUDE.md 踩坑 #33
updated. family_smoke.sh Check 4b now installs `mcp==1.10.0` to catch this
regression class.

## v1.5.27 (2026-05-20)

**Loosen Python requirement: now supports Python >= 3.10** — v1.5.25/26 fixed
the PEP 604 root cause in MCP tool signatures (Optional[X] instead of X | None),
but kept `requires-python = ">=3.11"` and a 3.11 hard guard in `mcp_cmd`. Both
relaxed to 3.10 so users on Python 3.10 (e.g. Goose default sandbox, Ubuntu
22.04 system python) can install and run directly without a Python upgrade.

- `pyproject.toml`: `requires-python = ">=3.10"` (was `>=3.11`; VMware-VKS
  was `>=3.12`, now also `>=3.10` for family alignment).
- `<pkg>/cli.py` `mcp_cmd()`: version guard now triggers on `< (3, 10)`.
- Behavior on Python 3.10 matches 3.11/3.12 — the Optional[X] fix from v1.5.25
  is what actually enables this; this release just stops blocking installs.

---

## v1.5.26

**Family-wide MCP server fix — Python 3.10 compatibility (踩坑 #33)** — `vmware-vks mcp`
crashed at decorator time on Python 3.10 with `subclass() arg 1 must be a class`.
Root cause: `mcp_server/server.py` used PEP 604 `X | None` in tool signatures
plus `from __future__ import annotations`; on Python 3.10 + older mcp/pydantic
combos, `typing.get_type_hints()` evaluates `"str | None"` to a
`types.UnionType` instance, which FastMCP/Pydantic then feeds to `issubclass()`.
Reported by a goose user (qwen3.6:27, Python 3.10).

- `mcp_server/server.py`: all `X | None` → `Optional[X]`; ops layer untouched.
- `<pkg>/cli.py` `mcp_cmd()`: hard guard — exits with installation fix command
  if Python < 3.11 (defense in depth, our actual lower bound).
- `pyproject.toml`: `mcp[cli]>=1.10,<2.0` (was `>=1.0`) so uv doesn't pick
  an ancient version that has the same issubclass bug.

**Tooling — family smoke gains MCP schema-build check** — `scripts/family_smoke.sh`
new Check 4b runs `asyncio.run(mcp.list_tools())` per skill, forcing FastMCP to
build Pydantic models for every declared tool. Supports both module-level `mcp`
and `build_server()` factory patterns.

**Docs — CLAUDE.md gains 踩坑 #33 (PEP 604 / Python 3.10) and #34 (CLI/MCP exposure parity).**

---

## v1.5.24 (2026-05-19)

**Fix — pyVmomi 8.x compatibility (踩坑 #32)** — `connection.py` previously set
`si._vmware_<skill>_verify_ssl = ...` on the pyVmomi `ServiceInstance`. pyVmomi 8.x
rejects attribute writes on `ManagedObject` with `Managed object attributes are
read-only`, which surfaced as `vmware-<skill> doctor` → `vSphere authentication: Auth
failed: Managed object attributes are read-only` on vCenter 8.0U3 even though raw
`SmartConnect()` worked fine.

- `connection.py`: introduce module-level `_SI_VERIFY_SSL: dict[int, bool]` keyed by
  `id(si)` plus `get_verify_ssl(si)` helper. Cleanup is wired into the same `atexit`
  hook that runs `Disconnect`.
- Downstream consumers (`ops/guest_ops.py`, `ops/vm_deploy.py`, `ops/supervisor.py`)
  switched from `getattr(si, "_vmware_*_verify_ssl", True)` to `get_verify_ssl(si)`.
- `scripts/family_smoke.sh`: new cross-skill check forbids `setattr` on pyVmomi
  ManagedObjects across the entire family (catches the same regression in future).

## v1.5.23 (2026-05-19)

**VCF 9.0 / 9.1 — partial / unverified compatibility note.**

- **docs:** README Version Compatibility table now explicitly lists vSphere 9.0 / 9.1 as ⚠ Not yet verified — Workload Management (Supervisor / WCP) API surface in vSphere 9 has not been tested by maintainers. Existing vSphere 8.x code paths should work but no guarantees until a lab run is completed. Users on vSphere 9 are encouraged to file issues with `check_vks_compatibility` output.
- **docs:** Added pointer to [VCF Python SDK](https://developer.broadcom.com/sdks) (the unified SDK in VCF 9+).
- **align:** Family v1.5.23 — all 9 skills tracking VCF 9.0 / 9.1 compatibility declaration.

## v1.5.22 (2026-05-08)

**Family alignment** — no source changes in this skill.

- **align:** Tracks v1.5.22 family bump driven by Smithery onboarding for vmware-avi / vmware-harden / vmware-pilot.

## v1.5.21 (2026-05-08)

**Family alignment** — no source changes in this skill.

- **deps:** Bumped `python-multipart` 0.0.26 → 0.0.27 (transitive, fixes GHSA HIGH DoS via unbounded multipart headers).
- **align:** Tracks v1.5.21 family bump driven by vmware-monitor folder_path feature (community PR #11).

## v1.5.20 (2026-05-08)

**Family alignment** — no source changes in this skill.

- **align:** Tracks v1.5.20 family bump driven by vmware-nsx-security and vmware-aria PyPI README `mcp-name:` ownership marker fix required by MCP Registry validation. Other 7 skills already had the marker; this release re-publishes them to keep the family version aligned per CLAUDE.md policy.
- **registry:** All 9 skills now registered on registry.modelcontextprotocol.io as `isLatest=true`.

## v1.5.19 (2026-05-06)

**Critical fix** — `delete_tkc_cluster` no longer leaks the Kubernetes ApiClient connection.

- **fix(ops):** `vmware_vks/ops/tkc.py:delete_tkc_cluster` now wraps `api.delete_namespaced_custom_object(...)` in `try/finally: api.api_client.close()`. Sibling functions (list/get/scale/upgrade/create) were already correct — only the delete path was missing the cleanup. Repeated deletes previously could exhaust the ApiClient connection pool (yjs review 2026-05-06).
- **smoke:** Family `scripts/family_smoke.sh` now recursively walks every Typer subcommand to trigger lazy imports.
- **align:** Family version bump to v1.5.19.

## v1.5.18 (2026-05-02)

**Security + compatibility fixes from external code review (2026-05-02 by Hermes Agent / MiniMax-M2.7)**

- **security:** `k8s_connection.py` and `ops/tkc.py::_check_running_workloads` — kubeconfig (with vCenter session bearer token) is no longer written to a temp file. New `_build_supervisor_kubeconfig()` and `ops/kubeconfig.py::build_tkc_kubeconfig()` return dicts; the kubernetes client loads them via `load_kube_config_from_dict()`. Eliminates the TOCTOU window where a credential file existed on disk between create and unlink.
- **compat:** `ops/tkc.py` — TKC API version is now resolved at runtime via the K8s discovery API. `_resolve_tkc_version()` prefers `cluster.x-k8s.io/v1` when the Supervisor serves it, falls back to `v1beta1` (vSphere 8.0). Result is cached per vCenter host. `generate_tkc_yaml()` accepts an optional `api_version` parameter (defaults to `v1beta1` for backwards compatibility). vSphere 8.0 environments are unaffected; later releases auto-upgrade with no code change.
- **dev:** `[dependency-groups]` block aligned to the canonical family set (`pytest>=8.0,<10.0`, `pytest-cov`, `ruff`).
- **test:** new `tests/eval/regression/test_release_blockers.py` enforces that the wheel ships `mcp_server`, every module imports cleanly, the Typer app loads, and runtime names like `re.match()` always have a matching `import re`.
- **align:** Family version bump to v1.5.18.

Tests: 5/5 release-blocker evals + 3/3 kubeconfig + 2/2 connection pass; pre-existing `test_no_destructive_ops` failure on `delete_namespace` unchanged from v1.5.17.

## v1.5.17 (2026-05-01)

**Family alignment** — no source changes in this skill.

This release tracks vmware-pilot v1.5.17 (new `investigate_alert` template + `review_workflow` MCP tool + `parallel_group` step type) and vmware-policy v1.5.17 (L5 pattern matcher integrated into `@vmware_tool`). Both work with the existing skill MCP surface unchanged.

- **align:** Family version bump to v1.5.17.

## v1.5.16 (2026-04-30)

**Enterprise Harness Engineering alignment** — adapted from the Linkloud × addxai framework articles ([part 1](https://mp.weixin.qq.com/s/hz4W7ILHJ1yz_pG0Z1xP-A), [part 2](https://mp.weixin.qq.com/s/F3qYbyB3S8oIqx-Y4BrWNQ)).

- **docs:** "Automation Level Reference" section in `references/capabilities.md` — every tool tagged L1-L5 per the EHE framework.
- **docs:** Common Workflows in `SKILL.md` rewritten with pre-flight judgment for TKC deploy (storage policy + VM class + quota), worker scaling (drain before scale-down), and namespace quota changes.
- **align:** Family version bump to v1.5.16.

## v1.5.15 (2026-04-29)

**UX improvements from real user feedback**

- **feat:** New top-level CLI subcommand `vmware-vks mcp` starts the MCP server. Single command, single binary on PATH after `uv tool install vmware-vks` — no more `uvx --from`, no PyPI re-resolve, no TLS-proxy issues.
- **feat:** Default `verify_ssl: true` on new targets (was `false`). Self-signed cert environments must now opt in explicitly with `verify_ssl: false` in `config.yaml`. Strengthens default security posture.
- **docs:** README, SKILL.md, setup-guide.md, and `examples/mcp-configs/*.json` switched to `command: "vmware-vks"`, `args: ["mcp"]`. uvx form moved to fallback with TLS-proxy troubleshooting note.
- **compat:** Legacy `vmware-vks-mcp` console script kept — existing user configs continue to work.

## v1.5.14 (2026-04-21)

**Bug fixes from code review by @yjs-2026 (follow-up)**

- **fix:** `tkc.py` — all 8 functions using K8s ApiClient now close it via try/finally, preventing urllib3 connection pool leaks in long-lived MCP servers
- **fix:** `storage.py` — same ApiClient leak fix for `list_namespace_storage_usage`
- **fix:** `kubeconfig.py` — same ApiClient leak fix for `get_tkc_kubeconfig_str`

## v1.5.13 (2026-04-21)

- Align with VMware skill family v1.5.13 (code review bug fixes)

## v1.5.12 (2026-04-17)

- Align with VMware skill family v1.5.12 (security & bug fixes from code review by @yjs-2026)

## v1.5.11 (2026-04-17)

- Align with VMware skill family v1.5.11 (AVI 22.x fixes from @timwangbc)

## v1.5.10 (2026-04-16)

- Security: bump python-multipart 0.0.22→0.0.26 (DoS via large multipart preamble/epilogue)
- Align with VMware skill family v1.5.10

## v1.5.9 (2026-04-15)

- Docs: corrected vSphere version requirement in SKILL.md routing table from "vSphere 7.x+" to "vSphere 8.x+" (matches the `_MIN_VERSION = (8, 0, 0)` enforced in `vmware_vks/ops/supervisor.py`). The earlier TKGS / vSphere with Tanzu product worked on 7.x; the "VKS" rebrand applies to the vSphere 8.x+ Workload Management API surface this skill targets.
- Docs: branding cleanup from external contributor — TKGS → VKS, NSX-T → NSX, HAProxy → Avi Networks (#6, credit @ryanconley1).

## v1.5.8 (2026-04-15)

- Align with VMware skill family v1.5.8 (NSX/AVI/Aria/AIops bug fixes)

## v1.5.7 (2026-04-15)

- Fix: VKS REST calls hardcoded `ssl.CERT_NONE` regardless of `target.verify_ssl` config, silently ignoring users who opted in to certificate verification. Added `_build_ssl_context(si)` that honours the trust preference recorded on the ServiceInstance by the connection manager.
- Fix: VKS REST calls had no timeout — default global socket timeout could hang the session on unreachable endpoints. Added `timeout=30` seconds on all REST requests (override via `VMWARE_VKS_REST_TIMEOUT`).
- Refactor: de-duplicated `_rest_post/_rest_patch/_rest_delete` helpers into a single `_rest_request(method, path, body)` in supervisor.py; namespace.py now imports from supervisor.
- Align with VMware skill family v1.5.7

## v1.5.6 (2026-04-15)

- Align with VMware skill family v1.5.6 (AVI bugfixes + packaging hotfix)

## v1.5.5 (2026-04-15)

- Align with VMware skill family v1.5.5

## v1.5.4 (2026-04-14)

- Security: bump pytest 9.0.2→9.0.3 (CVE-2025-71176, insecure tmpdir handling)

## v1.5.0 (2026-04-12)

### Anthropic Best Practices Integration

- **[READ]/[WRITE] tool prefixes**: All MCP tool descriptions now start with [READ] or [WRITE] to clearly indicate operation type
- **Read/write split counts**: SKILL.md MCP Tools section header shows exact read vs write tool counts
- **Negative routing**: Description frontmatter includes "Do NOT use when..." clause to prevent misrouting
- **Broadcom author attestation**: README.md, README-CN.md, and pyproject.toml include VMware by Broadcom author identity (wei-wz.zhou@broadcom.com) to resolve Snyk E005 brand warnings

### VKS-specific

- **Kubeconfig security notes**: get_supervisor_kubeconfig and get_tkc_kubeconfig docstrings include credential handling warnings (Snyk W007)

## v1.4.9 (2026-04-11)

- Fix: require explicit VMware/vSphere context in skill routing triggers (prevent false triggers on generic "clone", "deploy", "alarms" etc.)
- Fix: clarify vmware-policy compatibility field (Python transitive dep, not a required standalone binary)

## v1.4.8 (2026-04-09)

- Security: bump cryptography 46.0.6→46.0.7 (CVE-2026-39892, buffer overflow)
- Security: bump urllib3 2.3.0→2.6.3 (multiple CVEs) [VMware-VKS]
- Security: bump requests 2.32.5→2.33.0 (medium CVE) [VMware-VKS]

## v1.4.7 (2026-04-08)

- Fix: align openclaw metadata with actual runtime requirements
- Fix: standardize audit log path to ~/.vmware/audit.db across all docs
- Fix: update credential env var docs to correct VMWARE_<TARGET>_PASSWORD convention
- Fix: declare .env config and vmware-policy optional dependency in metadata

# Release Notes

## v1.4.5 — 2026-04-03

- **Security**: bump pygments 2.19.2 → 2.20.0 (fix ReDoS CVE in GUID matching regex)
- **Infrastructure**: add uv.lock for reproducible builds and Dependabot security tracking


## v1.4.6 — 2026-04-06

- fix: remove suspicious content from SKILL.md for ClawHub clean scan

---

## v1.4.0 — 2026-03-29

### Architecture: Unified Audit & Policy

- **vmware-policy integration**: All MCP tools now wrapped with `@vmware_tool` decorator
- **Unified audit logging**: Operations logged to `~/.vmware/audit.db` (SQLite WAL), replacing per-skill JSON Lines logs
- **Policy enforcement**: `check_allowed()` with rules.yaml, maintenance windows, risk-level gating
- **Sanitize consolidation**: Replaced local `_sanitize()` with shared `vmware_policy.sanitize()`
- **Risk classification**: Each tool tagged with risk_level (low/medium/high) for confirmation gating
- **Agent detection**: Audit logs identify calling agent (Claude/Codex/local)
- **New family members**: vmware-policy (audit/policy infrastructure) + vmware-pilot (workflow orchestration)


## v1.4.6 — 2026-04-06

- fix: remove suspicious content from SKILL.md for ClawHub clean scan

---

## v1.3.1 — 2026-03-27

### Family expansion: NSX, NSX-Security, Aria

- Added vmware-nsx, vmware-nsx-security, vmware-aria to companion skills routing table
- README updated with complete 7-skill family table
- vmware-aiops is now the family entry point (`vmware-aiops hub status`)


## v1.4.6 — 2026-04-06

- fix: remove suspicious content from SKILL.md for ClawHub clean scan

---

## v1.3.0 — 2026-03-26

### Docs / Skill optimization

- SKILL.md restructured with progressive disclosure (3-level loading)
- Created `references/` directory: cli-reference.md, capabilities.md, setup-guide.md
- Added trigger phrases to YAML description for better skill auto-loading
- Added Common Workflows section (Deploy TKC, Scale workers, Namespace management)
- Added Troubleshooting section (6 common issues)
- README.md and README-CN.md updated with Companion Skills, Workflows, Troubleshooting


## v1.4.6 — 2026-04-06

- fix: remove suspicious content from SKILL.md for ClawHub clean scan

---

## v0.1.0 (2026-03-25)

### New Features

**Phase 1 — Supervisor + Namespace (9 tools)**
- `check_vks_compatibility` — vCenter version check + WCP status
- `get_supervisor_status` — Supervisor cluster status and K8s API endpoint
- `list_supervisor_storage_policies` — Available storage policies
- `list_namespaces` — All vSphere Namespaces with status
- `get_namespace` — Namespace detail (quotas, storage, roles)
- `create_namespace` — Create Namespace with dry-run preview
- `update_namespace` — Modify quotas and storage policy
- `delete_namespace` — Delete with TKC guard (rejects if clusters exist)
- `list_vm_classes` — Available VM classes for TKC sizing

**Phase 2 — TKC + Access (11 tools)**
- `list_tkc_clusters` — TanzuKubernetesCluster list with status
- `get_tkc_cluster` — Cluster detail (nodes, health, conditions)
- `get_tkc_available_versions` — Supported K8s versions on Supervisor
- `create_tkc_cluster` — Create TKC with YAML plan + dry-run default
- `scale_tkc_cluster` — Scale worker node count
- `upgrade_tkc_cluster` — Upgrade K8s version
- `delete_tkc_cluster` — Delete with workload guard
- `get_supervisor_kubeconfig` — Supervisor kubeconfig YAML
- `get_tkc_kubeconfig` — TKC kubeconfig (stdout or file)
- `get_harbor_info` — Embedded Harbor registry info
- `list_namespace_storage_usage` — PVC list and capacity stats

**CLI**
- `vmware-vks check` — Pre-flight diagnostics
- `vmware-vks supervisor status|storage-policies`
- `vmware-vks namespace list|get|create|update|delete|vm-classes`
- `vmware-vks tkc list|get|versions|create|scale|upgrade|delete`
- `vmware-vks kubeconfig supervisor|get`
- `vmware-vks harbor`
- `vmware-vks storage`

### Requirements
- vSphere 8.0+ with Workload Management (Supervisor) enabled
- vSphere with Tanzu license (Enterprise Plus or VCF)