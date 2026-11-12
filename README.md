<!-- mcp-name: io.github.vmware-skills/vmware-vks -->

# VMware-VKS

Work with Supervisor and VKS Kubernetes lifecycle using discovered APIs.

English · [简体中文](README-CN.md)

Community-maintained · MIT · Not an official VMware/Broadcom product.

[![18-second illustrative investigation demo](https://raw.githubusercontent.com/vmware-skills/marketplace/main/assets/demos/vmware-skills-en.gif)](https://github.com/vmware-skills/marketplace/blob/main/assets/demos/vmware-skills-en.mp4)

Synthetic-data animation, not a live recording. [Watch the AI-narrated family introduction (~2 min)](https://github.com/vmware-skills/marketplace/blob/main/assets/demos/vmware-skills-en.mp4)

## Why use it?

- Inspect namespaces, VM classes and storage policies.
- Use explicit ClusterClass and server validation where supported.
- Expose conditions and discovery gaps instead of guessing.

## Get started

Install the published CLI and inspect its commands:

```sh
uv tool install vmware-vks==3.7.2
vmware-vks --help
```

Requires uv and Python >=3.10. Configure targets and credentials locally before remote queries. [Setup guide](skills/vmware-vks/references/setup-guide.md).

For Claude Code and other host installation paths, see the [family portal](https://github.com/vmware-skills/marketplace).

## Know the boundaries

Includes lifecycle writes and kubeconfig export. API/GVK support is target-specific; package availability is not a claim of positive 9.1/Supervisor live validation.

## Go deeper

- [Capabilities](skills/vmware-vks/references/capabilities.md)
- [CLI reference](skills/vmware-vks/references/cli-reference.md)
- [Agent guardrails](skills/vmware-vks/references/agent-guardrails.md)
- [Detailed usage and architecture](skills/vmware-vks/references/detailed-guide.md)
- [Canonical skill instructions](skills/vmware-vks/SKILL.md)

Part of [VMware Skills](https://github.com/vmware-skills/marketplace): 15 skills, 14 MCP servers and one shared Policy library. If it helps your work, star the family portal.

[Issues and feedback](https://github.com/vmware-skills/VMware-VKS/issues) · [MIT](LICENSE)
