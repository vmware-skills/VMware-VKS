"""Clusters MCP registrations sharing the server runtime.

The facade owns connection, error and Policy state; this module creates no second
server or connection manager. Returned aliases preserve the original Python API.
"""

from typing import Optional


def register_tools(mcp, runtime):
    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    @runtime.vmware_tool(risk_level="low")
    def list_tkc_clusters(
        namespace: Optional[str] = None, target: Optional[str] = None
    ) -> dict:
        """[READ] List TanzuKubernetesCluster (TKC) clusters, optionally in one namespace.

        Returns the family list envelope: {items: [{name, namespace, phase,
        k8s_version}], returned, limit, total, truncated, hint}. The Supervisor
        list is walked to completion, so truncated is always False. Start here, then call get_tkc_cluster for full detail or
        issue_tkc_kubeconfig for access. 'clusters' is a deprecated pre-1.8.0 alias
        of 'items', removed in 2.0 — read 'items'.

        Args:
            namespace: vSphere Namespace to filter by. Omit to list every one.
            target: vCenter in config.yaml; omit for the default.
        """
        try:
            si = runtime._get_si(target)
            from vmware_vks.ops import tkc as _tkc

            return _tkc.list_tkc_clusters(si, namespace=namespace)
        except Exception as e:
            return runtime._tool_error(e)

    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    @runtime.vmware_tool(risk_level="low")
    def get_tkc_cluster(
        name: str, namespace: str, target: Optional[str] = None
    ) -> dict:
        """[READ] Get detailed status for a single TKC cluster.

        Returns one object, not the list envelope: name, namespace, phase,
        k8s_version, control_plane_replicas, worker_pools,
        worker_replica_total (null if any pool is autoscaled or unknown), conditions,
        infrastructure_ready, control_plane_ready. The legacy worker_replicas field
        names only the first pool. Run list_tkc_clusters first — a
        TKC name is only unique within one namespace. Poll this after
        create_tkc_cluster, scale_tkc_cluster or upgrade_tkc_cluster to watch an
        async change land.

        Args:
            name: Cluster name (via list_tkc_clusters).
            namespace: Namespace holding it.
            target: vCenter in config.yaml; omit for the default.
        """
        try:
            si = runtime._get_si(target)
            from vmware_vks.ops import tkc as _tkc

            return _tkc.get_tkc_cluster(si, name, namespace)
        except Exception as e:
            return runtime._tool_error(e)

    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    @runtime.vmware_tool(risk_level="low")
    def get_tkc_available_versions(
        namespace: str, target: Optional[str] = None
    ) -> dict:
        """[READ] List Kubernetes versions (TanzuKubernetesReleases) available on the Supervisor.

        Returns {versions: [{name, version, e.g. 'v1.28.4+vmware.1'}]}, newest
        first. If the TanzuKubernetesRelease API is unavailable it returns an
        empty versions list with error and hint rather than raising. Call this
        before create_tkc_cluster or upgrade_tkc_cluster to pick a valid
        k8s_version.

        Args:
            namespace: vSphere Namespace used to reach the Supervisor K8s API.
            target: vCenter in config.yaml; omit for the default.
        """
        try:
            si = runtime._get_si(target)
            from vmware_vks.ops import tkc as _tkc

            return _tkc.get_tkc_available_versions(si, namespace)
        except Exception as e:
            return runtime._tool_error(e)

    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    @runtime.vmware_tool(
        risk_level="medium",
        undo=runtime._acted_undo(
            "created",
            "delete_tkc_cluster",
            "Inverse of create_tkc_cluster: delete the TKC cluster.",
            ("name", "namespace", "target"),
        ),
    )
    def create_tkc_cluster(
        name: str,
        namespace: str,
        k8s_version: str,
        vm_class: str,
        control_plane_count: int = 1,
        worker_count: int = 3,
        storage_class: str = "vsphere-storage",
        confirm: bool = False,
        target: Optional[str] = None,
        cluster_class: Optional[str] = None,
        cluster_class_namespace: Optional[str] = None,
    ) -> dict:
        """[WRITE] Create a TanzuKubernetesCluster in a vSphere Namespace.

        Without confirm=True this only previews: it returns blast_radius (node
        counts, VM class, the YAML manifest, and whether the name is taken) and
        creates nothing. Show it to the user and get their decision. Do not set
        confirm=True on your own because the user asked earlier — they have not
        seen the preview yet. confirm=True returns {action: "created", name,
        namespace, status: "creating", yaml, blast_radius} and provisions in the
        background — poll get_tkc_cluster and inspect its conditions/readiness. Refused when the
        name is taken or the namespace's clusters could not be read. Call
        get_tkc_available_versions for k8s_version and list_vm_classes first.
        Requires a discovered v1beta1 or v1beta2 API. Preview and apply run
        Supervisor admission with dryRun=All and strict field validation; rejection
        stops creation. v1beta2 requires an explicit installed cluster_class; use
        cluster_class_namespace for shared classes. No automatic rebase or variable
        conversion. vSphere/VCF 9.1 end-to-end deployment is not lab validated.

        Args:
            name: Cluster name.
            namespace: Must already exist (see list_namespaces).
            k8s_version: e.g. 'v1.28.4+vmware.1'.
            vm_class: Node sizing, e.g. 'best-effort-large'.
            control_plane_count: 1 or 3.
            worker_count: Worker nodes (>= 1).
            storage_class: Storage class.
            cluster_class: Installed ClusterClass; required for v1beta2.
            cluster_class_namespace: Class namespace, e.g. vmware-system-vks-public.
            confirm: False (default) returns the blast radius and changes nothing. True applies it.
            target: vCenter in config.yaml; omit for the default.
        """
        from vmware_vks.ops import tkc as _tkc

        t = target or "default"
        params = {
            "k8s_version": k8s_version,
            "vm_class": vm_class,
            "workers": worker_count,
        }
        decision = runtime._gate.decide_create(confirm)
        try:
            si = runtime._get_si(target)
            radius = runtime._gate.measure_tkc_create(
                si,
                name,
                namespace,
                k8s_version,
                vm_class,
                control_plane_count,
                worker_count,
                storage_class,
                cluster_class=cluster_class,
                cluster_class_namespace=cluster_class_namespace,
            )
            if not decision.act:
                return runtime._preview(radius, "create")
            runtime._gate.refuse_if_blocked("create_tkc_cluster", name, radius)
            result = _tkc.create_tkc_cluster(
                si,
                name=name,
                namespace=namespace,
                k8s_version=k8s_version,
                vm_class=vm_class,
                control_plane_count=control_plane_count,
                worker_count=worker_count,
                storage_class=storage_class,
                dry_run=False,
                cluster_class=cluster_class,
                cluster_class_namespace=cluster_class_namespace,
            )
        except Exception as e:
            if decision.act:
                runtime._audit.log(
                    target=t,
                    operation="create_tkc_cluster",
                    resource=f"{namespace}/{name}",
                    parameters=params,
                    result=f"error: {runtime.sanitize(str(e), 200)}",
                )
            return runtime._gate_error(
                e,
                "create_tkc_cluster",
                (
                    "Verify k8s_version with get_tkc_available_versions and "
                    "vm_class with list_vm_classes; the namespace must already "
                    "exist (list_namespaces)."
                ),
            )
        runtime._audit.log(
            target=t,
            operation="create_tkc_cluster",
            resource=f"{namespace}/{name}",
            parameters=params,
            result="success",
        )
        return {"action": "created", **result, "blast_radius": radius}

    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    @runtime.vmware_tool(risk_level="medium")
    def scale_tkc_cluster(
        name: str,
        namespace: str,
        worker_count: int,
        pool_name: Optional[str] = None,
        target: Optional[str] = None,
    ) -> dict:
        """[WRITE] Scale the worker node count of an existing TanzuKubernetesCluster (TKC).

        Asynchronous: returns {name, namespace, pool, worker_count, status:
        "scaling"} immediately — poll get_tkc_cluster to watch nodes appear or
        drain. Scales workers only; use upgrade_tkc_cluster instead for the K8s
        version. Not destructive, but lowering worker_count drains removed nodes.

        Args:
            name: Cluster name (via list_tkc_clusters).
            namespace: Namespace holding it.
            worker_count: Desired total, integer >= 1 (below 1 is rejected).
            pool_name: Node pool (machineDeployment). Omit for the first;
                other pools are always preserved.
            target: vCenter in config.yaml; omit for the default.
        """
        from vmware_vks.ops import tkc as _tkc

        t = target or "default"
        params = {"worker_count": worker_count, "pool_name": pool_name}
        try:
            si = runtime._get_si(target)
            result = _tkc.scale_tkc_cluster(
                si, name, namespace, worker_count, pool_name=pool_name
            )
        except Exception as e:
            runtime._audit.log(
                target=t,
                operation="scale_tkc_cluster",
                resource=f"{namespace}/{name}",
                parameters=params,
                result=f"error: {runtime.sanitize(str(e), 200)}",
            )
            return {
                "error": runtime._safe_error(e, "scale_tkc_cluster"),
                "hint": (
                    "Run get_tkc_cluster for the cluster's current phase and "
                    "replica counts. No tool lists node pool names; an unknown "
                    "pool_name is rejected with the available names in the error."
                ),
            }
        runtime._audit.log(
            target=t,
            operation="scale_tkc_cluster",
            resource=f"{namespace}/{name}",
            parameters=params,
            result="success",
        )
        return result

    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    @runtime.vmware_tool(risk_level="medium")
    def upgrade_tkc_cluster(
        name: str, namespace: str, k8s_version: str, target: Optional[str] = None
    ) -> dict:
        """[WRITE] Upgrade a TKC cluster to a new Kubernetes version.

        Returns {name, namespace, new_version, status: "upgrading"}. Asynchronous
        and irreversible — Kubernetes cannot be downgraded, so poll
        get_tkc_cluster until phase is running. There is no dry run. Use this only
        for the K8s version; prefer scale_tkc_cluster for node counts.

        Args:
            name: Cluster name (via list_tkc_clusters).
            namespace: Namespace holding it.
            k8s_version: Target version from get_tkc_available_versions.
            target: vCenter in config.yaml; omit for the default.
        """
        from vmware_vks.ops import tkc as _tkc

        t = target or "default"
        try:
            si = runtime._get_si(target)
            result = _tkc.upgrade_tkc_cluster(si, name, namespace, k8s_version)
        except Exception as e:
            runtime._audit.log(
                target=t,
                operation="upgrade_tkc_cluster",
                resource=f"{namespace}/{name}",
                parameters={"k8s_version": k8s_version},
                result=f"error: {runtime.sanitize(str(e), 200)}",
            )
            return {
                "error": runtime._safe_error(e, "upgrade_tkc_cluster"),
                "hint": (
                    "Run get_tkc_available_versions for the versions this "
                    "Supervisor offers, and get_tkc_cluster for the cluster's "
                    "current phase."
                ),
            }
        runtime._audit.log(
            target=t,
            operation="upgrade_tkc_cluster",
            resource=f"{namespace}/{name}",
            parameters={"k8s_version": k8s_version},
            result="success",
        )
        return result

    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        }
    )
    @runtime.vmware_tool(risk_level="high")
    def delete_tkc_cluster(
        name: str,
        namespace: str,
        confirm: bool = False,
        force: bool = False,
        target: Optional[str] = None,
    ) -> dict:
        """[WRITE] Delete a TKC cluster and all of its nodes (irreversible).

        Without confirm=True this only previews: it returns blast_radius (control
        plane and worker node counts per pool, and the running Deployments,
        StatefulSets and DaemonSets) and deletes nothing. Show that to the user and
        get their explicit decision. Do not set confirm=True on your own because
        the user said "delete" earlier: they have not seen what it destroys yet.

        confirm=True returns {action: "deleted", name, namespace, status:
        "deleting", blast_radius}. Refused while workloads run (drain them, or pass
        force=True, which skips the workload check entirely) and whenever the node
        counts or workloads could not be read. Use scale_tkc_cluster instead for
        fewer nodes. Empty a namespace of TKC clusters before delete_namespace
        accepts it.

        Args:
            name: Cluster name (via list_tkc_clusters).
            namespace: Namespace holding it.
            confirm: False (default) returns the blast radius and changes nothing. True applies it.
            force: Skip the running-workload check (dangerous).
            target: vCenter in config.yaml; omit for the default.
        """
        from vmware_vks.ops import tkc as _tkc

        t = target or "default"
        decision = runtime._gate.decide_delete(confirm)
        try:
            si = runtime._get_si(target)
            radius = runtime._gate.measure_tkc_delete(si, name, namespace, force)
            if not decision.act:
                return runtime._preview(radius, "delete")
            runtime._gate.refuse_if_blocked(
                "delete_tkc_cluster", f"{namespace}/{name}", radius
            )
            # The gate has just read the workloads (or force skips them); the
            # executor's own check would repeat the read, so it is told to skip it.
            result = _tkc.delete_tkc_cluster(
                si,
                name,
                namespace,
                confirmed=True,
                dry_run=False,
                force=True,
            )
        except Exception as e:
            if decision.act:
                runtime._audit.log(
                    target=t,
                    operation="delete_tkc_cluster",
                    resource=f"{namespace}/{name}",
                    parameters={"force": force},
                    result=f"error: {runtime.sanitize(str(e), 200)}",
                )
            return runtime._gate_error(
                e,
                "delete_tkc_cluster",
                (
                    "Run get_tkc_cluster for the cluster's phase and conditions, "
                    "or list_tkc_clusters to confirm name and namespace. Preview "
                    "delete_tkc_cluster (without confirm) to see the running "
                    "workloads that block it, or pass force=True to skip the check."
                ),
            )
        runtime._audit.log(
            target=t,
            operation="delete_tkc_cluster",
            resource=f"{namespace}/{name}",
            parameters={"force": force},
            result="success",
        )
        return {"action": "deleted", **result, "blast_radius": radius}

    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    @runtime.vmware_tool(risk_level="medium", sensitive_result=True)
    def issue_supervisor_kubeconfig(
        namespace: str,
        output_path: Optional[str] = None,
        target: Optional[str] = None,
    ) -> dict:
        """[WRITE] Credential access: issue a kubeconfig for the Supervisor K8s API.

        Call only when the user explicitly asks for this kubeconfig; never as a
        side step. Returns {namespace, kubeconfig} as a YAML string, or
        {namespace, written_to} when output_path is given. The kubeconfig embeds
        a Supervisor bearer token (JWT from /wcp/login) that acts as the
        configured vCenter account until the JWT expires (typically hours; not
        tied to this process) — always pass output_path so the token never
        enters agent context, and report only the path. The file is created
        owner-only (0600). For TKC workloads, see issue_tkc_kubeconfig and its
        vSphere 9.1 limitation before issuing a guest-cluster credential.

        Args:
            namespace: vSphere Namespace to set as the kubeconfig context.
            output_path: File to write, e.g. '~/.kube/supervisor.yaml'. Omit to
                return the kubeconfig inline. Creates parent directories and
                truncates the file.
            target: vCenter in config.yaml; omit for the default.
        """
        try:
            from pathlib import Path

            si = runtime._get_si(target)
            from vmware_vks.ops import kubeconfig as _kc

            path = Path(output_path).expanduser() if output_path else None
            return _kc.write_supervisor_kubeconfig(si, namespace, output_path=path)
        except Exception as e:
            return runtime._tool_error(e)

    return (
        list_tkc_clusters,
        get_tkc_cluster,
        get_tkc_available_versions,
        create_tkc_cluster,
        scale_tkc_cluster,
        upgrade_tkc_cluster,
        delete_tkc_cluster,
        issue_supervisor_kubeconfig,
    )
