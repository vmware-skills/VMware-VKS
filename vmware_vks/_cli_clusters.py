"""Cli Clusters callbacks using the existing facade runtime."""

from __future__ import annotations


from typing import Optional


def register_callbacks(runtime):
    @runtime.tkc_app.command("list")
    @runtime._cli_errors
    @runtime.audited("list_tkc_clusters")
    def tkc_list(
        namespace: Optional[str] = runtime.typer.Option(None, "-n", "--namespace"),
        target: Optional[str] = runtime.typer.Option(None, "-t", "--target"),
    ):
        """List TKC clusters (optionally filtered by namespace)."""
        from vmware_vks.ops.tkc import list_tkc_clusters

        si = runtime._get_si(target)
        result = list_tkc_clusters(si, namespace=namespace)
        table = runtime.Table("Name", "Namespace", "Phase", "K8s Version")
        for c in result["items"]:
            table.add_row(c["name"], c["namespace"], c["phase"], c["k8s_version"])
        runtime.console.print(f"Total: {result['total']}")
        runtime.console.print(table)

    @runtime.tkc_app.command("get")
    @runtime._cli_errors
    @runtime.audited("get_tkc_cluster")
    def tkc_get(
        name: str = runtime.typer.Argument(...),
        namespace: str = runtime.typer.Option(..., "-n", "--namespace"),
        target: Optional[str] = runtime.typer.Option(None, "-t", "--target"),
    ):
        """Get detailed info for a TKC cluster."""
        import json

        from vmware_vks.ops.tkc import get_tkc_cluster

        si = runtime._get_si(target)
        result = get_tkc_cluster(si, name, namespace)
        runtime.console.print_json(json.dumps(result))

    @runtime.tkc_app.command("versions")
    @runtime._cli_errors
    @runtime.audited("get_tkc_available_versions")
    def tkc_versions(
        namespace: str = runtime.typer.Option(
            ..., "-n", "--namespace", help="vSphere Namespace"
        ),
        target: Optional[str] = runtime.typer.Option(None, "-t", "--target"),
    ):
        """List available K8s versions for TKC clusters."""
        from vmware_vks.ops.tkc import get_tkc_available_versions

        si = runtime._get_si(target)
        result = get_tkc_available_versions(si, namespace)
        if result.get("error"):
            runtime.console.print(f"[yellow]{result['error']}[/yellow]")
            runtime.console.print(f"[dim]{result.get('hint', '')}[/dim]")
            return
        table = runtime.Table("Version")
        for v in result["versions"]:
            table.add_row(v["version"])
        runtime.console.print(table)

    @runtime.tkc_app.command("create")
    @runtime._cli_errors
    @runtime.guarded("create_tkc_cluster", risk_level="medium")
    def tkc_create(
        name: str = runtime.typer.Argument(...),
        namespace: str = runtime.typer.Option(..., "-n", "--namespace"),
        k8s_version: Optional[str] = runtime.typer.Option(None, "--version"),
        vm_class: Optional[str] = runtime.typer.Option(None, "--vm-class"),
        control_plane: int = runtime.typer.Option(1, "--control-plane"),
        workers: int = runtime.typer.Option(3, "--workers"),
        storage_policy: str = runtime.typer.Option(
            "vsphere-storage", "--storage-policy"
        ),
        cluster_class: Optional[str] = runtime.typer.Option(None, "--cluster-class"),
        cluster_class_namespace: Optional[str] = runtime.typer.Option(
            None, "--cluster-class-namespace"
        ),
        api_version: str = runtime.typer.Option(
            "v1beta1", "--api-version", help="Offline template schema"
        ),
        apply: bool = runtime.typer.Option(
            False, "--apply", help="Apply (default: dry-run)"
        ),
        target: Optional[str] = runtime.typer.Option(None, "-t", "--target"),
    ):
        """Create a TKC cluster (dry-run by default, use --apply to create).

        Missing --version or --vm-class triggers interactive prompts.
        """
        import json

        from vmware_vks.ops.namespace import list_vm_classes
        from vmware_vks.ops.tkc import create_tkc_cluster, get_tkc_available_versions

        si = runtime._get_si(target)

        # Interactive prompts for missing params
        if not k8s_version:
            versions_result = get_tkc_available_versions(si, namespace)
            version_choices = [
                v["version"] for v in versions_result.get("versions", [])
            ]
            if version_choices:
                runtime.console.print(
                    f"Available versions: {', '.join(version_choices[:5])}"
                )
            k8s_version = runtime.typer.prompt("K8s version")

        if not vm_class:
            classes = list_vm_classes(si)["items"]
            class_choices = [c["id"] for c in classes if c.get("id")]
            if class_choices:
                runtime.console.print(
                    f"Available VM classes: {', '.join(class_choices[:5])}"
                )
            vm_class = runtime.typer.prompt("VM class")

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
                cluster_class=cluster_class,
                cluster_class_namespace=cluster_class_namespace,
                api_version=api_version,
            )
        except Exception as e:
            if apply:
                runtime._audit_cli(
                    target,
                    "create_tkc_cluster",
                    f"{namespace}/{name}",
                    params,
                    f"failed: {e}",
                )
            raise
        if apply:
            runtime._audit_cli(
                target, "create_tkc_cluster", f"{namespace}/{name}", params, "success"
            )
        runtime.console.print_json(json.dumps(result))

    @runtime.tkc_app.command("scale")
    @runtime._cli_errors
    @runtime.guarded("scale_tkc_cluster", risk_level="medium")
    def tkc_scale(
        name: str = runtime.typer.Argument(...),
        namespace: str = runtime.typer.Option(..., "-n", "--namespace"),
        workers: int = runtime.typer.Option(..., "--workers"),
        pool: Optional[str] = runtime.typer.Option(
            None,
            "--pool",
            help="Node pool (machineDeployment) name; defaults to the first pool",
        ),
        target: Optional[str] = runtime.typer.Option(None, "-t", "--target"),
    ):
        """Scale TKC cluster worker node count (other node pools are preserved)."""
        import json

        from vmware_vks.ops.tkc import scale_tkc_cluster

        si = runtime._get_si(target)
        params = {"worker_count": workers, "pool": pool}
        try:
            result = scale_tkc_cluster(si, name, namespace, workers, pool_name=pool)
        except Exception as e:
            runtime._audit_cli(
                target,
                "scale_tkc_cluster",
                f"{namespace}/{name}",
                params,
                f"failed: {e}",
            )
            raise
        runtime._audit_cli(
            target, "scale_tkc_cluster", f"{namespace}/{name}", params, "success"
        )
        runtime.console.print_json(json.dumps(result))

    @runtime.tkc_app.command("upgrade")
    @runtime._cli_errors
    @runtime.guarded("upgrade_tkc_cluster", risk_level="medium")
    def tkc_upgrade(
        name: str = runtime.typer.Argument(...),
        namespace: str = runtime.typer.Option(..., "-n", "--namespace"),
        version: str = runtime.typer.Option(..., "--version"),
        target: Optional[str] = runtime.typer.Option(None, "-t", "--target"),
    ):
        """Upgrade TKC cluster to a new K8s version."""
        import json

        from vmware_vks.ops.tkc import upgrade_tkc_cluster

        si = runtime._get_si(target)
        params = {"k8s_version": version}
        try:
            result = upgrade_tkc_cluster(si, name, namespace, version)
        except Exception as e:
            runtime._audit_cli(
                target,
                "upgrade_tkc_cluster",
                f"{namespace}/{name}",
                params,
                f"failed: {e}",
            )
            raise
        runtime._audit_cli(
            target, "upgrade_tkc_cluster", f"{namespace}/{name}", params, "success"
        )
        runtime.console.print_json(json.dumps(result))

    @runtime.tkc_app.command("delete")
    @runtime._cli_errors
    @runtime.guarded("delete_tkc_cluster", risk_level="high")
    def tkc_delete(
        name: str = runtime.typer.Argument(...),
        namespace: str = runtime.typer.Option(..., "-n", "--namespace"),
        skip_workload_check: bool = runtime.typer.Option(
            False,
            "--skip-workload-check",
            help="Skip the running-workload guard (dangerous). The interactive "
            "name confirmation is always required.",
        ),
        target: Optional[str] = runtime.typer.Option(None, "-t", "--target"),
    ):
        """Delete a TKC cluster (rejects if workloads running)."""
        import json

        from vmware_vks.ops.tkc import delete_tkc_cluster

        si = runtime._get_si(target)

        # Show dry-run first
        dry = delete_tkc_cluster(
            si, name, namespace, confirmed=True, dry_run=True, force=skip_workload_check
        )
        runtime.console.print_json(json.dumps(dry))

        if not runtime._double_confirm(name, "TKC cluster"):
            runtime.console.print("[yellow]Aborted.[/yellow]")
            raise runtime.typer.Exit(1)

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
            runtime._audit_cli(
                target,
                "delete_tkc_cluster",
                f"{namespace}/{name}",
                params,
                f"failed: {e}",
            )
            raise
        runtime._audit_cli(
            target, "delete_tkc_cluster", f"{namespace}/{name}", params, "success"
        )
        runtime.console.print_json(json.dumps(result))

    return (
        tkc_list,
        tkc_get,
        tkc_versions,
        tkc_create,
        tkc_scale,
        tkc_upgrade,
        tkc_delete,
    )
