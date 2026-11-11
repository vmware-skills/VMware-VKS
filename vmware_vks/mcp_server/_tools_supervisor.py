"""Supervisor MCP registrations sharing the server runtime.

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
    def check_vks_compatibility(target: Optional[str] = None) -> dict:
        """[READ] Check whether this vCenter supports VKS (requires vSphere 8.x+).

        Returns compatible (bool), vcenter_version, wcp_enabled_clusters
        and wcp_clusters ({cluster, status}). Start here: those cluster MoRefs are
        the cluster_id for get_supervisor_status and create_namespace. Only
        reports vCenter-level support — a listed cluster may still be CONFIGURING.

        Args:
            target: vCenter in config.yaml; omit for the default.
        """
        try:
            si = runtime._get_si(target)
            from vmware_vks.ops import supervisor as _sup

            return _sup.check_vks_compatibility(si)
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
    def get_supervisor_upgrade_risk(
        target_version: str = "9.1.1", target: Optional[str] = None
    ) -> dict:
        """[READ] Check the vCenter 9.1.0/9.1.1 Supervisor pre-upgrade advisory scope.

        Reads the source vCenter version/build and Supervisor cluster inventory.
        Returns advisory_applicability, supervisor_presence and next_step. The
        vLCM/VUM database schema is **unmeasured**: this tool never grants upgrade
        clearance. Use the official checker in Broadcom KB 455737 or VDT before
        upgrading an affected environment. No database script is run here.

        Args:
            target_version: Planned vCenter version, 9.1.0 or 9.1.1.
            target: vCenter target from config.yaml; omit for the default.
        """
        try:
            from vmware_vks.ops.upgrade_risk import (
                get_supervisor_upgrade_risk as report,
            )

            return report(runtime._get_si(target), target_version)
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
    def get_supervisor_status(cluster_id: str, target: Optional[str] = None) -> dict:
        """[READ] Get the health of one Supervisor Cluster (vSphere with Tanzu control plane).

        Returns cluster_id, config_status (RUNNING = healthy, else CONFIGURING /
        ERROR / REMOVING), kubernetes_status (READY / WARNING / ERROR),
        api_server_cluster_endpoint, kubernetes_version (null plus
        kubernetes_version_hint if unavailable), and network_provider. Run
        check_vks_compatibility first for cluster IDs; use this to confirm a
        Supervisor is healthy before create_namespace or create_tkc_cluster.
        On vCenter 9.1+, optional version lookup requires Namespaces.Manage for
        Supervisor topology and Namespaces.Upgrade for version evidence. Missing
        access leaves the version unknown; it does not grant or bypass RBAC.

        Args:
            cluster_id: Compute cluster MoRef, e.g. 'domain-c1' (wcp_clusters
                field of check_vks_compatibility).
            target: vCenter in config.yaml; omit for the default.
        """
        try:
            si = runtime._get_si(target)
            from vmware_vks.ops import supervisor as _sup

            return _sup.get_supervisor_status(si, cluster_id)
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
    def list_supervisor_storage_policies(target: Optional[str] = None) -> dict:
        """[READ] List vCenter storage policies assignable to Supervisor Namespaces.

        Returns the list envelope: items of {policy (ID), name, description} plus
        returned/total/truncated — one call returns them all, so truncated is
        always false. Call this before create_namespace or update_namespace and
        pass the 'policy' ID as their storage_policy. For PVC-level usage use
        list_namespace_storage_usage instead.

        Args:
            target: vCenter in config.yaml; omit for the default.
        """
        try:
            si = runtime._get_si(target)
            from vmware_vks.ops import supervisor as _sup

            return _sup.list_supervisor_storage_policies(si)
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
    def list_namespaces(target: Optional[str] = None) -> dict:
        """[READ] List all vSphere Namespaces on the target vCenter with their status.

        Returns the list envelope: items of {namespace, config_status (RUNNING =
        healthy, CONFIGURING, REMOVING, ERROR), description} plus
        returned/total/truncated — one call returns them all, so truncated is
        always false. Start here, then call get_namespace for detail,
        list_tkc_clusters for what runs inside, or update_namespace /
        delete_namespace to change one.

        Args:
            target: vCenter in config.yaml; omit for the default.
        """
        try:
            si = runtime._get_si(target)
            from vmware_vks.ops import namespace as _ns

            return _ns.list_namespaces(si)
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
    def get_namespace(name: str, target: Optional[str] = None) -> dict:
        """[READ] Get detailed configuration for a single vSphere Namespace.

        Returns one raw vCenter namespace object, not the list envelope:
        config_status, description, storage_specs, quotas. Use
        list_namespaces first for the name; follow with
        list_namespace_storage_usage for PVC usage or list_tkc_clusters for the
        clusters inside. Point-in-time only — a CONFIGURING namespace may
        not have quotas applied.

        Args:
            name: Namespace name, e.g. 'dev' (discover via list_namespaces).
            target: vCenter in config.yaml; omit for the default.
        """
        try:
            si = runtime._get_si(target)
            from vmware_vks.ops import namespace as _ns

            return _ns.get_namespace(si, name)
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
            "delete_namespace",
            "Inverse of create_namespace: delete the namespace.",
            ("name", "target"),
        ),
    )
    def create_namespace(
        name: str,
        cluster_id: str,
        storage_policy: str,
        cpu_limit: Optional[int] = None,
        memory_limit_mib: Optional[int] = None,
        description: str = "",
        confirm: bool = False,
        target: Optional[str] = None,
    ) -> dict:
        """[WRITE] Create a vSphere Namespace on a Supervisor Cluster.

        Without confirm=True this only previews: it returns blast_radius (the spec
        that would be applied, and whether the name is already taken) and creates
        nothing. Show it to the user and get their decision. Do not set
        confirm=True on your own because the user asked earlier — they have not
        seen the preview yet. confirm=True returns {action: "created", namespace,
        status, cluster, blast_radius}; it is refused when the name is taken (use
        update_namespace) or the existing names could not be read. Confirm with
        get_namespace afterwards.

        Args:
            name: Namespace name (lowercase, no spaces).
            cluster_id: Supervisor MoRef (from check_vks_compatibility).
            storage_policy: Policy ID (from list_supervisor_storage_policies).
            cpu_limit: MHz. Omit for no limit.
            memory_limit_mib: MiB. Omit for no limit.
            description: Free-text label. Omit for none.
            confirm: False (default) returns the blast radius and changes nothing. True applies it.
            target: vCenter in config.yaml; omit for the default.
        """
        from vmware_vks.ops import namespace as _ns

        t = target or "default"
        params = {"cluster_id": cluster_id, "storage_policy": storage_policy}
        decision = runtime._gate.decide_create(confirm)
        try:
            si = runtime._get_si(target)
            spec = _ns.create_namespace(
                si,
                name=name,
                cluster_id=cluster_id,
                storage_policy=storage_policy,
                cpu_limit=cpu_limit,
                memory_limit_mib=memory_limit_mib,
                description=description,
                dry_run=True,
            )["spec"]
            radius = runtime._gate.measure_namespace_create(si, name, spec)
            if not decision.act:
                return runtime._preview(radius, "create")
            runtime._gate.refuse_if_blocked("create_namespace", name, radius)
            result = _ns.create_namespace(
                si,
                name=name,
                cluster_id=cluster_id,
                storage_policy=storage_policy,
                cpu_limit=cpu_limit,
                memory_limit_mib=memory_limit_mib,
                description=description,
                dry_run=False,
            )
        except Exception as e:
            if decision.act:
                runtime._audit.log(
                    target=t,
                    operation="create_namespace",
                    resource=name,
                    parameters=params,
                    result=f"error: {runtime.sanitize(str(e), 200)}",
                )
            return runtime._gate_error(
                e,
                "create_namespace",
                (
                    "Verify cluster_id with check_vks_compatibility and "
                    "storage_policy with list_supervisor_storage_policies; "
                    "list_namespaces shows whether the name is already taken."
                ),
            )
        runtime._audit.log(
            target=t,
            operation="create_namespace",
            resource=name,
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
    def update_namespace(
        name: str,
        cpu_limit: Optional[int] = None,
        memory_limit_mib: Optional[int] = None,
        storage_policy: Optional[str] = None,
        target: Optional[str] = None,
    ) -> dict:
        """[WRITE] Update resource quotas or storage policy of an existing vSphere Namespace.

        Only the fields you pass are patched; omitting all of them returns status
        "no_changes" without an API call, otherwise {namespace, status:
        "updated"}. Applies immediately — no dry run, no undo. Use this rather
        than create_namespace when the namespace exists; valid storage_policy
        values come from list_supervisor_storage_policies.

        Args:
            name: Existing namespace name (discover via list_namespaces).
            cpu_limit: New CPU limit in MHz. Omit to keep current.
            memory_limit_mib: New memory limit in MiB. Omit to keep current.
            storage_policy: New storage policy ID. Omit to keep current.
            target: vCenter in config.yaml; omit for the default.
        """
        from vmware_vks.ops import namespace as _ns

        t = target or "default"
        try:
            si = runtime._get_si(target)
            result = _ns.update_namespace(
                si,
                name,
                cpu_limit=cpu_limit,
                memory_limit_mib=memory_limit_mib,
                storage_policy=storage_policy,
            )
        except Exception as e:
            runtime._audit.log(
                target=t,
                operation="update_namespace",
                resource=name,
                parameters={},
                result=f"error: {runtime.sanitize(str(e), 200)}",
            )
            return {
                "error": runtime._safe_error(e, "update_namespace"),
                "hint": (
                    "Run get_namespace for the namespace's current settings, or "
                    "list_supervisor_storage_policies for valid storage_policy "
                    "values."
                ),
            }
        runtime._audit.log(
            target=t,
            operation="update_namespace",
            resource=name,
            parameters={},
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
    def delete_namespace(
        name: str,
        confirm: bool = False,
        target: Optional[str] = None,
    ) -> dict:
        """[WRITE] Delete a vSphere Namespace and everything inside it (irreversible).

        Without confirm=True this only previews: it returns blast_radius (the
        namespace, and the TKC clusters, VMs and PVCs inside it, with counts and
        names) and deletes nothing. Show that to the user and get their explicit
        decision. Do not set confirm=True on your own because the user said
        "delete" earlier: they have not seen what it destroys yet.

        confirm=True returns {action: "deleted", namespace, status, blast_radius}.
        Refused while TKC clusters exist inside (run delete_tkc_cluster on each),
        and whenever the clusters, VMs or PVCs inside could not be read. Prefer
        update_namespace to only change quotas.

        Args:
            name: Namespace name to delete (discover via list_namespaces).
            confirm: False (default) returns the blast radius and changes nothing. True applies it.
            target: vCenter in config.yaml; omit for the default.
        """
        from vmware_vks.ops import namespace as _ns

        t = target or "default"
        decision = runtime._gate.decide_delete(confirm)
        try:
            si = runtime._get_si(target)
            radius = runtime._gate.measure_namespace_delete(si, name)
            if not decision.act:
                return runtime._preview(radius, "delete")
            runtime._gate.refuse_if_blocked("delete_namespace", name, radius)
            result = _ns.delete_namespace(si, name, confirmed=True, dry_run=False)
        except Exception as e:
            if decision.act:
                runtime._audit.log(
                    target=t,
                    operation="delete_namespace",
                    resource=name,
                    parameters={},
                    result=f"error: {runtime.sanitize(str(e), 200)}",
                )
            return runtime._gate_error(
                e,
                "delete_namespace",
                (
                    "Run list_tkc_clusters — clusters inside the namespace block "
                    "the delete — and get_namespace to confirm it still exists."
                ),
            )
        runtime._audit.log(
            target=t,
            operation="delete_namespace",
            resource=name,
            parameters={},
            result="success",
        )
        return {"action": "deleted", **result, "blast_radius": radius}

    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    @runtime.vmware_tool(risk_level="low")
    def list_vm_classes(target: Optional[str] = None) -> dict:
        """[READ] List VM classes available for sizing TKC cluster nodes.

        Returns the list envelope: items of {id (e.g.
        'best-effort-large'), cpu_count, memory_mb, gpu_count (vGPU + DirectPath
        I/O; 0 if none)} plus returned/total/truncated — one call returns them
        all, so truncated is always false. Call this before create_tkc_cluster and
        pass the chosen 'id' as its vm_class; 'guaranteed-*' classes reserve
        resources, 'best-effort-*' do not.

        Args:
            target: vCenter in config.yaml; omit for the default.
        """
        try:
            si = runtime._get_si(target)
            from vmware_vks.ops import namespace as _ns

            return _ns.list_vm_classes(si)
        except Exception as e:
            return runtime._tool_error(e)

    return (
        check_vks_compatibility,
        get_supervisor_upgrade_risk,
        get_supervisor_status,
        list_supervisor_storage_policies,
        list_namespaces,
        get_namespace,
        create_namespace,
        update_namespace,
        delete_namespace,
        list_vm_classes,
    )
