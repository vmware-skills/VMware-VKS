"""Typer CLI for vmware-vks."""

from __future__ import annotations

import functools
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from vmware_policy import PolicyDenied, guarded

from vmware_vks.errors import VksApiError


def _harden_console_encoding() -> None:
    """Never let one unrepresentable glyph kill a command.

    On a console whose encoding cannot carry the characters we print -- cp936 on
    the Chinese Windows boxes this family is tested on, or any ASCII locale --
    ``print`` raises ``UnicodeEncodeError`` and the whole command dies with a
    traceback. ``--help`` died that way in four repos. A mangled dash is a
    cosmetic loss; a dead ``--help`` is an outage, so the error handler is
    relaxed rather than the vocabulary narrowed.

    Best effort: ``reconfigure`` is absent when stdout has been replaced by a
    plain object (pytest capture, some MCP hosts), and losing the hardening
    there is not worth an exception at import.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError, OSError):
            pass


_harden_console_encoding()

app = typer.Typer(name="vmware-vks", help="vSphere with Tanzu (VKS) management CLI")
supervisor_app = typer.Typer(help="Supervisor cluster commands")
namespace_app = typer.Typer(help="Namespace commands")

app.add_typer(supervisor_app, name="supervisor")
app.add_typer(namespace_app, name="namespace")

tkc_app = typer.Typer(help="TanzuKubernetesCluster commands")
kubeconfig_app = typer.Typer(help="Kubeconfig commands")
vm_app = typer.Typer(help="VM Service (vm-operator) read commands")

app.add_typer(tkc_app, name="tkc")
app.add_typer(kubeconfig_app, name="kubeconfig")
app.add_typer(vm_app, name="vm")


@app.command("mcp")
def mcp_cmd() -> None:
    """Start the MCP server (stdio transport).

    Single-command entry point for MCP clients:
        vmware-vks mcp

    Equivalent to the legacy `vmware-vks-mcp` console script.
    """
    import sys

    if sys.version_info < (3, 10):
        msg = (
            f"ERROR: vmware-vks MCP server requires Python >= 3.10 "
            f"(got {sys.version_info.major}.{sys.version_info.minor}).\n"
            f"Interpreter: {sys.executable}\n"
            "Fix: uv python install 3.12 && "
            "uv tool install --python 3.12 --force vmware-vks"
        )
        typer.echo(msg, err=True)
        raise typer.Exit(2)

    from vmware_vks.mcp_server.server import main as _mcp_main

    _mcp_main()


console = Console()


def _get_si(target: str | None = None):
    from vmware_vks.config import load_config
    from vmware_vks.connection import ConnectionManager

    # No env-var read here: load_config resolves the path (explicit arg, then
    # the environment, then the default). This was one of five copies of that
    # rule, and load_config's copy was the one that did not look at the
    # variable — so the surfaces disagreed about which file was in force.
    config = load_config()
    mgr = ConnectionManager(config)
    return mgr.connect(target)


def _cli_errors(fn):
    """Translate expected failures into a red one-liner + exit code 1.

    Centralized error handling (踩坑 #37): users see a teaching message,
    never a raw traceback. typer.Exit/Abort pass through untouched (they
    subclass RuntimeError via click).

    Auth failures get a teaching message pointing at the exact file + env var:
    pyVmomi ``vim.fault.InvalidLogin`` is the primary vCenter SmartConnect
    failure; ``VksApiError`` with status 401/403 covers the Supervisor
    ``/wcp/login`` (WCP-REST) and Kubernetes API paths. ``ssl.SSLError``
    teaches the ``verify_ssl: false`` toggle for self-signed lab certs.
    """
    import ssl

    from pyVmomi import vim

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (typer.Exit, typer.Abort):
            raise
        except PolicyDenied as e:
            # A deny rule or maintenance window refused this write — @guarded ran
            # guard() before the body and already wrote the status="denied" audit
            # row. Teach the operator which rule fired instead of a traceback.
            rule = f" [dim](rule: {e.result.rule})[/]" if e.result.rule else ""
            console.print(f"[red]Denied by policy: {e.result.reason}[/]{rule}")
            raise typer.Exit(1) from e
        except ssl.SSLError as e:
            # Self-signed lab certs are the usual cause; teach the toggle.
            console.print(
                f"[red]Error: TLS certificate verification failed: {e} → For a "
                "self-signed lab, set verify_ssl: false on the target in "
                "~/.vmware-vks/config.yaml (or re-run 'vmware-vks init').[/red]"
            )
            raise typer.Exit(1) from e
        except vim.fault.InvalidLogin as e:
            # The SDK message ("incorrect user name or password") doesn't say
            # WHERE to fix it — point at the exact file + env var.
            console.print(
                "[red]Error: Login failed (incorrect username or password). → "
                "Check the password in ~/.vmware-vks/.env (env var "
                "VMWARE_VKS_<TARGET>_PASSWORD) and the username in "
                "~/.vmware-vks/config.yaml. Re-run 'vmware-vks init' to reset "
                "both.[/red]"
            )
            raise typer.Exit(1) from e
        except VksApiError as e:
            message = str(e)
            if getattr(e, "status_code", None) in (401, 403):
                # Supervisor /wcp/login or K8s API auth denial — point at the
                # same credential sources as the pyVmomi path.
                message = (
                    f"{message} → Check the password in ~/.vmware-vks/.env (env "
                    "var VMWARE_VKS_<TARGET>_PASSWORD) and the username in "
                    "~/.vmware-vks/config.yaml. Re-run 'vmware-vks init' to "
                    "reset both."
                )
            console.print(f"[red]Error: {message}[/red]")
            raise typer.Exit(1) from e
        except (FileNotFoundError, KeyError, OSError, RuntimeError) as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1) from e

    return wrapper


def _audit_cli(
    target: Optional[str],
    operation: str,
    resource: str,
    parameters: dict,
    result: str,
) -> None:
    """Best-effort CLI write audit — failure degrades to a stderr warning."""
    try:
        from vmware_vks.notify.audit import AuditLogger

        AuditLogger().log(
            target=target or "default",
            operation=operation,
            resource=resource,
            parameters=parameters,
            result=result,
        )
    except Exception as e:  # never block the main operation on audit failure
        print(f"Warning: audit log write failed: {e}", file=sys.stderr)


def _double_confirm(resource: str, resource_type: str = "resource") -> bool:
    console.print(
        f"[red]WARNING: You are about to delete {resource_type} '{resource}'.[/red]"
    )
    typed = typer.prompt(f"Type '{resource}' to confirm")
    return typed == resource


@app.command("init")
def cmd_init(
    force: bool = typer.Option(
        False, "--force", help="Overwrite an existing config without prompting first"
    ),
    skip_test: bool = typer.Option(
        False, "--skip-test", help="Don't run a connection test after writing config"
    ),
):
    """Guided first-run setup: write config.yaml + .env (grep-safe), then verify.

    Prompts for the vCenter target, stores the password obfuscated as ``b64:``
    in ~/.vmware-vks/.env (0600, never plaintext), and offers to test the
    connection. Re-run with --force to reconfigure.
    """
    from vmware_vks.init_wizard import run_init

    raise typer.Exit(run_init(force=force, skip_test=skip_test))


# Registered under both names on purpose. Every other skill in the family calls
# this `doctor`, and documentation, troubleshooting steps and habit all say
# "run doctor"; someone following those against VKS got "No such command" and
# no hint that the identical check was one word away. The body is shared, so the
# two can never drift into meaning different things.
@app.command("doctor")
@app.command("check")
def cmd_check(
    config: Optional[Path] = typer.Option(None, help="Path to config.yaml"),
):
    """Run pre-flight checks (config, passwords, vCenter version, WCP)."""
    from vmware_vks.doctor import run_doctor

    ok = run_doctor(config)
    raise typer.Exit(0 if ok else 1)


@app.command("preflight-auth")
@_cli_errors
def cmd_preflight_auth(
    target: Optional[str] = typer.Option(
        None, "-t", "--target", help="Target name (default: all configured targets)"
    ),
):
    """Live-validate the Supervisor POST /wcp/login bearer-token flow (issue #13).

    Runs the REAL login against the configured Supervisor (not a mock) and
    reports, per target: vCenter reachable? /wcp/login HTTP status? parseable
    'session_id'? does the JWT authenticate a trivial Supervisor K8s API call?
    Each failure prints a teaching message. Exit code is non-zero if any step
    fails — run this in your environment to close out issue #13.
    """
    from vmware_vks.preflight_auth import run_preflight_auth

    if target:
        names: list[Optional[str]] = [target]
    else:
        from vmware_vks.config import load_config

        config = load_config()
        names = [t.name for t in config.targets] or [None]

    all_passed = True
    for name in names:
        result = run_preflight_auth(name)
        table = Table(
            title=f"Supervisor /wcp/login preflight — target '{result.target}'",
            show_header=True,
        )
        table.add_column("Step", style="bold")
        table.add_column("Status")
        table.add_column("Detail", overflow="fold")
        for step in result.steps:
            status = "[green]✓ PASS[/green]" if step.ok else "[red]✗ FAIL[/red]"
            table.add_row(step.name, status, step.detail)
        console.print(table)
        if result.passed:
            console.print(
                f"[green]✓ target '{result.target}': /wcp/login auth flow "
                "validated end-to-end.[/green]"
            )
        else:
            all_passed = False

    raise typer.Exit(0 if all_passed else 1)


@supervisor_app.command("status")
@_cli_errors
def supervisor_status(
    cluster_id: str = typer.Argument(
        ..., help="Compute cluster MoRef (e.g. domain-c1)"
    ),
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """Get Supervisor Cluster status."""
    from vmware_vks.ops.supervisor import get_supervisor_status

    si = _get_si(target)
    result = get_supervisor_status(si, cluster_id)
    for k, v in result.items():
        console.print(f"  [bold]{k}:[/bold] {v}")


@supervisor_app.command("storage-policies")
@_cli_errors
def supervisor_storage_policies(
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """List vCenter storage policies available for Namespaces."""
    from vmware_vks.ops.supervisor import list_supervisor_storage_policies

    si = _get_si(target)
    policies = list_supervisor_storage_policies(si)["items"]
    table = Table("Policy ID", "Name", "Description")
    for p in policies:
        table.add_row(str(p["policy"]), p["name"], p["description"])
    console.print(table)


@namespace_app.command("list")
@_cli_errors
def namespace_list(
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """List all vSphere Namespaces."""
    from vmware_vks.ops.namespace import list_namespaces

    si = _get_si(target)
    nss = list_namespaces(si)["items"]
    table = Table("Namespace", "Status", "Description")
    for ns in nss:
        table.add_row(ns["namespace"], ns["config_status"], ns.get("description", ""))
    console.print(table)


@namespace_app.command("get")
@_cli_errors
def namespace_get(
    name: str = typer.Argument(...),
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """Get details for a single Namespace."""
    import json

    from vmware_vks.ops.namespace import get_namespace

    si = _get_si(target)
    result = get_namespace(si, name)
    console.print_json(json.dumps(result))


@namespace_app.command("create")
@_cli_errors
@guarded(risk_level='medium')
def namespace_create(
    name: str = typer.Argument(...),
    cluster_id: str = typer.Option(..., "--cluster", help="Supervisor cluster MoRef"),
    storage_policy: str = typer.Option(..., "--storage-policy"),
    cpu_limit: Optional[int] = typer.Option(None, "--cpu"),
    memory_mib: Optional[int] = typer.Option(None, "--memory"),
    description: str = typer.Option("", "--description"),
    apply: bool = typer.Option(False, "--apply", help="Apply (default: dry-run)"),
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """Create a vSphere Namespace (dry-run by default, use --apply to create)."""
    import json

    from vmware_vks.ops.namespace import create_namespace

    si = _get_si(target)
    params = {"cluster_id": cluster_id, "storage_policy": storage_policy}
    try:
        result = create_namespace(
            si,
            name=name,
            cluster_id=cluster_id,
            storage_policy=storage_policy,
            cpu_limit=cpu_limit,
            memory_limit_mib=memory_mib,
            description=description,
            dry_run=not apply,
        )
    except Exception as e:
        if apply:
            _audit_cli(target, "create_namespace", name, params, f"failed: {e}")
        raise
    if apply:
        _audit_cli(target, "create_namespace", name, params, "success")
    console.print_json(json.dumps(result))


@namespace_app.command("update")
@_cli_errors
@guarded(risk_level='medium')
def namespace_update(
    name: str = typer.Argument(...),
    cpu_limit: Optional[int] = typer.Option(None, "--cpu"),
    memory_mib: Optional[int] = typer.Option(None, "--memory"),
    storage_policy: Optional[str] = typer.Option(None, "--storage-policy"),
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """Update Namespace resource quotas or storage policy."""
    import json

    from vmware_vks.ops.namespace import update_namespace

    si = _get_si(target)
    result = update_namespace(
        si,
        name,
        cpu_limit=cpu_limit,
        memory_limit_mib=memory_mib,
        storage_policy=storage_policy,
    )
    console.print_json(json.dumps(result))


@namespace_app.command("delete")
@_cli_errors
@guarded(risk_level='high')
def namespace_delete(
    name: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force", help="Skip interactive confirm"),
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """Delete a vSphere Namespace (rejects if TKC clusters exist inside)."""
    import json

    from vmware_vks.ops.namespace import delete_namespace

    si = _get_si(target)
    # Show dry-run first
    dry = delete_namespace(si, name, confirmed=True, dry_run=True)
    console.print_json(json.dumps(dry))

    if not force:
        if not _double_confirm(name, "namespace"):
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(1)

    try:
        result = delete_namespace(si, name, confirmed=True, dry_run=False)
    except Exception as e:
        _audit_cli(target, "delete_namespace", name, {}, f"failed: {e}")
        raise
    _audit_cli(target, "delete_namespace", name, {}, "success")
    console.print_json(json.dumps(result))


@namespace_app.command("vm-classes")
@_cli_errors
def namespace_vm_classes(
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """List available VM classes for TKC node sizing."""
    from vmware_vks.ops.namespace import list_vm_classes

    si = _get_si(target)
    classes = list_vm_classes(si)["items"]
    table = Table("ID", "CPU", "Memory (MB)", "GPU")
    for c in classes:
        table.add_row(
            str(c["id"]), str(c["cpu_count"]), str(c["memory_mb"]), str(c["gpu_count"])
        )
    console.print(table)


# ---------------------------------------------------------------------------
# TKC commands
# ---------------------------------------------------------------------------


@tkc_app.command("list")
@_cli_errors
def tkc_list(
    namespace: Optional[str] = typer.Option(None, "-n", "--namespace"),
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """List TKC clusters (optionally filtered by namespace)."""
    from vmware_vks.ops.tkc import list_tkc_clusters

    si = _get_si(target)
    result = list_tkc_clusters(si, namespace=namespace)
    table = Table("Name", "Namespace", "Phase", "K8s Version")
    for c in result["items"]:
        table.add_row(c["name"], c["namespace"], c["phase"], c["k8s_version"])
    console.print(f"Total: {result['total']}")
    console.print(table)


@tkc_app.command("get")
@_cli_errors
def tkc_get(
    name: str = typer.Argument(...),
    namespace: str = typer.Option(..., "-n", "--namespace"),
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """Get detailed info for a TKC cluster."""
    import json

    from vmware_vks.ops.tkc import get_tkc_cluster

    si = _get_si(target)
    result = get_tkc_cluster(si, name, namespace)
    console.print_json(json.dumps(result))


@tkc_app.command("versions")
@_cli_errors
def tkc_versions(
    namespace: str = typer.Option(..., "-n", "--namespace", help="vSphere Namespace"),
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """List available K8s versions for TKC clusters."""
    from vmware_vks.ops.tkc import get_tkc_available_versions

    si = _get_si(target)
    result = get_tkc_available_versions(si, namespace)
    if result.get("error"):
        console.print(f"[yellow]{result['error']}[/yellow]")
        console.print(f"[dim]{result.get('hint', '')}[/dim]")
        return
    table = Table("Version")
    for v in result["versions"]:
        table.add_row(v["version"])
    console.print(table)


@tkc_app.command("create")
@_cli_errors
@guarded(risk_level='medium')
def tkc_create(
    name: str = typer.Argument(...),
    namespace: str = typer.Option(..., "-n", "--namespace"),
    k8s_version: Optional[str] = typer.Option(None, "--version"),
    vm_class: Optional[str] = typer.Option(None, "--vm-class"),
    control_plane: int = typer.Option(1, "--control-plane"),
    workers: int = typer.Option(3, "--workers"),
    storage_policy: str = typer.Option("vsphere-storage", "--storage-policy"),
    apply: bool = typer.Option(False, "--apply", help="Apply (default: dry-run)"),
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """Create a TKC cluster (dry-run by default, use --apply to create).

    Missing --version or --vm-class triggers interactive prompts.
    """
    import json

    from vmware_vks.ops.namespace import list_vm_classes
    from vmware_vks.ops.tkc import create_tkc_cluster, get_tkc_available_versions

    si = _get_si(target)

    # Interactive prompts for missing params
    if not k8s_version:
        versions_result = get_tkc_available_versions(si, namespace)
        version_choices = [v["version"] for v in versions_result.get("versions", [])]
        if version_choices:
            console.print(f"Available versions: {', '.join(version_choices[:5])}")
        k8s_version = typer.prompt("K8s version")

    if not vm_class:
        classes = list_vm_classes(si)["items"]
        class_choices = [c["id"] for c in classes if c.get("id")]
        if class_choices:
            console.print(f"Available VM classes: {', '.join(class_choices[:5])}")
        vm_class = typer.prompt("VM class")

    params = {"k8s_version": k8s_version, "vm_class": vm_class, "workers": workers}
    try:
        result = create_tkc_cluster(
            si,
            name=name,
            namespace=namespace,
            k8s_version=k8s_version,
            vm_class=vm_class,
            control_plane_count=control_plane,
            worker_count=workers,
            storage_class=storage_policy,
            dry_run=not apply,
        )
    except Exception as e:
        if apply:
            _audit_cli(
                target,
                "create_tkc_cluster",
                f"{namespace}/{name}",
                params,
                f"failed: {e}",
            )
        raise
    if apply:
        _audit_cli(
            target, "create_tkc_cluster", f"{namespace}/{name}", params, "success"
        )
    console.print_json(json.dumps(result))


@tkc_app.command("scale")
@_cli_errors
@guarded(risk_level='medium')
def tkc_scale(
    name: str = typer.Argument(...),
    namespace: str = typer.Option(..., "-n", "--namespace"),
    workers: int = typer.Option(..., "--workers"),
    pool: Optional[str] = typer.Option(
        None,
        "--pool",
        help="Node pool (machineDeployment) name; defaults to the first pool",
    ),
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """Scale TKC cluster worker node count (other node pools are preserved)."""
    import json

    from vmware_vks.ops.tkc import scale_tkc_cluster

    si = _get_si(target)
    params = {"worker_count": workers, "pool": pool}
    try:
        result = scale_tkc_cluster(si, name, namespace, workers, pool_name=pool)
    except Exception as e:
        _audit_cli(
            target, "scale_tkc_cluster", f"{namespace}/{name}", params, f"failed: {e}"
        )
        raise
    _audit_cli(target, "scale_tkc_cluster", f"{namespace}/{name}", params, "success")
    console.print_json(json.dumps(result))


@tkc_app.command("upgrade")
@_cli_errors
@guarded(risk_level='medium')
def tkc_upgrade(
    name: str = typer.Argument(...),
    namespace: str = typer.Option(..., "-n", "--namespace"),
    version: str = typer.Option(..., "--version"),
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """Upgrade TKC cluster to a new K8s version."""
    import json

    from vmware_vks.ops.tkc import upgrade_tkc_cluster

    si = _get_si(target)
    params = {"k8s_version": version}
    try:
        result = upgrade_tkc_cluster(si, name, namespace, version)
    except Exception as e:
        _audit_cli(
            target, "upgrade_tkc_cluster", f"{namespace}/{name}", params, f"failed: {e}"
        )
        raise
    _audit_cli(target, "upgrade_tkc_cluster", f"{namespace}/{name}", params, "success")
    console.print_json(json.dumps(result))


@tkc_app.command("delete")
@_cli_errors
@guarded(risk_level='high')
def tkc_delete(
    name: str = typer.Argument(...),
    namespace: str = typer.Option(..., "-n", "--namespace"),
    skip_workload_check: bool = typer.Option(
        False,
        "--skip-workload-check",
        help="Skip the running-workload guard (dangerous). The interactive "
        "name confirmation is always required.",
    ),
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """Delete a TKC cluster (rejects if workloads running)."""
    import json

    from vmware_vks.ops.tkc import delete_tkc_cluster

    si = _get_si(target)

    # Show dry-run first
    dry = delete_tkc_cluster(
        si, name, namespace, confirmed=True, dry_run=True, force=skip_workload_check
    )
    console.print_json(json.dumps(dry))

    if not _double_confirm(name, "TKC cluster"):
        console.print("[yellow]Aborted.[/yellow]")
        raise typer.Exit(1)

    params = {"skip_workload_check": skip_workload_check}
    try:
        result = delete_tkc_cluster(
            si,
            name,
            namespace,
            confirmed=True,
            dry_run=False,
            force=skip_workload_check,
        )
    except Exception as e:
        _audit_cli(
            target, "delete_tkc_cluster", f"{namespace}/{name}", params, f"failed: {e}"
        )
        raise
    _audit_cli(target, "delete_tkc_cluster", f"{namespace}/{name}", params, "success")
    console.print_json(json.dumps(result))


# ---------------------------------------------------------------------------
# VM Service (vm-operator) read commands
# ---------------------------------------------------------------------------


@vm_app.command("snapshots")
@_cli_errors
def vm_snapshots(
    namespace: str = typer.Option(..., "-n", "--namespace"),
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """List VirtualMachineSnapshot objects in a Namespace."""
    from vmware_vks.ops.vmservice import list_vm_snapshots

    si = _get_si(target)
    result = list_vm_snapshots(si, namespace)
    table = Table("Name", "VM", "Ready", "Created")
    for s in result["items"]:
        table.add_row(
            s["name"], s["vm_name"], str(s["ready"]), str(s["created"] or "")
        )
    console.print(f"Total: {result['total']} (served {result['served_version']})")
    console.print(table)


@vm_app.command("groups")
@_cli_errors
def vm_groups(
    namespace: str = typer.Option(..., "-n", "--namespace"),
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """List VirtualMachineGroup objects and their bootOrder in a Namespace."""
    from vmware_vks.ops.vmservice import list_vm_groups

    si = _get_si(target)
    result = list_vm_groups(si, namespace)
    table = Table("Name", "Members", "Boot Order")
    for g in result["items"]:
        stages = [
            "[" + ", ".join(m["name"] for m in stage["members"]) + "]"
            for stage in g["boot_order"]
        ]
        table.add_row(g["name"], str(g["member_count"]), " -> ".join(stages))
    console.print(f"Total: {result['total']} (served {result['served_version']})")
    console.print(table)


@vm_app.command("nics")
@_cli_errors
def vm_nics(
    vm_name: str = typer.Argument(...),
    namespace: str = typer.Option(..., "-n", "--namespace"),
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """List the network interfaces (multi-NIC) of one VirtualMachine."""
    from vmware_vks.ops.vmservice import list_vm_network_interfaces

    si = _get_si(target)
    result = list_vm_network_interfaces(si, namespace, vm_name)
    table = Table("Interface", "Network", "Kind")
    for nic in result["items"]:
        table.add_row(nic["name"], nic["network_name"], nic["network_kind"])
    console.print(f"VM: {result['vm_name']}  Total NICs: {result['total']}")
    console.print(table)


# ---------------------------------------------------------------------------
# Kubeconfig commands
# ---------------------------------------------------------------------------


@kubeconfig_app.command("supervisor")
@_cli_errors
def kubeconfig_supervisor(
    namespace: str = typer.Option(..., "-n", "--namespace"),
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """Get Supervisor kubeconfig."""
    from vmware_vks.ops.kubeconfig import get_supervisor_kubeconfig_str

    si = _get_si(target)
    kc = get_supervisor_kubeconfig_str(si, namespace)
    console.print(kc)


@kubeconfig_app.command("get")
@_cli_errors
@guarded(risk_level='low')
def kubeconfig_get(
    name: str = typer.Argument(...),
    namespace: str = typer.Option(..., "-n", "--namespace"),
    output: Optional[Path] = typer.Option(None, "--output", "-o"),
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """Get TKC cluster kubeconfig."""

    from vmware_vks.ops.kubeconfig import write_kubeconfig

    si = _get_si(target)
    result = write_kubeconfig(si, name, namespace, output_path=output)
    if output:
        console.print(f"[green]Written to {output}[/green]")
    else:
        console.print(result.get("kubeconfig", ""))


# ---------------------------------------------------------------------------
# Harbor command
# ---------------------------------------------------------------------------


@app.command("harbor")
@_cli_errors
def harbor_info(
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """Get Harbor registry info."""
    import json

    from vmware_vks.ops.harbor import get_harbor_info

    si = _get_si(target)
    result = get_harbor_info(si)
    console.print_json(json.dumps(result))


# ---------------------------------------------------------------------------
# Storage command
# ---------------------------------------------------------------------------


@app.command("storage")
@_cli_errors
def storage_usage(
    namespace: str = typer.Option(..., "-n", "--namespace"),
    target: Optional[str] = typer.Option(None, "-t", "--target"),
):
    """List PVC storage usage for a Namespace."""
    import json

    from vmware_vks.ops.storage import list_namespace_storage_usage

    si = _get_si(target)
    result = list_namespace_storage_usage(si, namespace)
    console.print_json(json.dumps(result))
