"""Access MCP registrations sharing the server runtime.

The facade owns connection, error and Policy state; this module creates no second
server or connection manager. Returned aliases preserve the original Python API.
"""

from typing import Optional


def register_tools(mcp, runtime):
    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }
    )
    @runtime.vmware_tool(risk_level="medium", sensitive_result=True)
    def issue_tkc_kubeconfig(
        name: str,
        namespace: str,
        output_path: Optional[str] = None,
        target: Optional[str] = None,
    ) -> dict:
        """[WRITE] Credential access: issue a kubeconfig for one TKC cluster.

        Call only when the user explicitly asks for this kubeconfig; never as a
        side step. Returns {cluster, kubeconfig}, or {cluster, written_to} when
        output_path is given. The kubeconfig embeds a Supervisor bearer token
        (JWT from /wcp/login) that acts as the configured vCenter account until
        the JWT expires (typically hours; not tied to this process) — always pass
        output_path so the token never enters agent context, and report only the path. Nothing in
        the managed cluster changes, but output_path creates parent directories
        and truncates the named file (owner-only, 0600), so
        output_path='~/.kube/config' replaces the user's own kubeconfig. Run
        list_tkc_clusters first for name and namespace; use
        issue_supervisor_kubeconfig instead for Supervisor-level access.
        On one reported VCF 9.1 deployment, the guest cluster API rejects this
        Supervisor JWT with 401. A generated file does not prove guest access;
        verify it against the guest API before relying on it (issue #27).

        Args:
            name: TKC cluster name.
            namespace: Namespace holding it.
            output_path: File to write, e.g. '~/.kube/my.yaml'. Omit to return
                the kubeconfig inline.
            target: vCenter in config.yaml; omit for the default.
        """
        try:
            from pathlib import Path

            si = runtime._get_si(target)
            from vmware_vks.ops import kubeconfig as _kc

            path = Path(output_path).expanduser() if output_path else None
            return _kc.write_kubeconfig(si, name, namespace, output_path=path)
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
    def get_harbor_info(target: Optional[str] = None) -> dict:
        """[READ] Get status of the embedded Harbor container registry on the Supervisor.

        Returns {registries: [{id, cluster (Supervisor MoRef), version, url,
        status, storage_used_mb}]}; status and storage come from a detail call and
        are null if it fails. If Harbor is not enabled it returns
        {error, hint} rather than raising. Use it to check registry health or find
        the push URL — it does not list repositories or images. Run
        check_vks_compatibility first if the Supervisor may be down.

        Args:
            target: vCenter in config.yaml; omit for the default.
        """
        try:
            si = runtime._get_si(target)
            from vmware_vks.ops import harbor as _harbor

            return _harbor.get_harbor_info(si)
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
    def list_namespace_storage_usage(
        namespace: str, target: Optional[str] = None
    ) -> dict:
        """[READ] List PersistentVolumeClaims and storage usage inside one vSphere Namespace.

        Via the Supervisor K8s API. Returns the family list envelope:
        {namespace, items: [{name, namespace, status (Bound / Pending / Lost),
        capacity ('10Gi'), storage_class}], returned, limit, total,
        truncated, hint}. Every PVC comes back in one call, so truncated is always
        False. Run list_namespaces first for the namespace; use
        list_supervisor_storage_policies instead for policy-level rather than
        PVC-level information. 'pvcs' and 'pvc_count' are deprecated pre-1.8.0
        aliases of 'items' and 'returned', removed in 2.0 — read 'items'.

        Args:
            namespace: Namespace to inspect.
            target: vCenter in config.yaml; omit for the default.
        """
        try:
            si = runtime._get_si(target)
            from vmware_vks.ops import storage as _storage

            return _storage.list_namespace_storage_usage(si, namespace)
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
    def list_vm_service_snapshots(namespace: str, target: Optional[str] = None) -> dict:
        """[READ] List VirtualMachineSnapshot objects in a vSphere Namespace.

        VM Service snapshots (vmoperator.vmware.com CRD, new at v1alpha5) via the
        Supervisor K8s API — the served CRD version is discovered at runtime, not
        hardcoded. Returns the family list envelope: items of {name, namespace,
        vm_name, created, ready} plus returned/total/truncated (walked to
        completion, so truncated is always false) and served_version. If the
        Supervisor is older than v1alpha5 the error names the required version. Run
        list_namespaces first for the namespace; use list_vm_service_network_interfaces for
        a VM's NICs.

        Args:
            namespace: vSphere Namespace to list snapshots in (via list_namespaces).
            target: vCenter in config.yaml; omit for the default.
        """
        try:
            si = runtime._get_si(target)
            from vmware_vks.ops import vmservice as _vmsvc

            return _vmsvc.list_vm_snapshots(si, namespace)
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
    def list_vm_service_groups(namespace: str, target: Optional[str] = None) -> dict:
        """[READ] List VirtualMachineGroup objects and their bootOrder in a Namespace.

        VM Service groups (vmoperator.vmware.com CRD, v1alpha4+) via the Supervisor
        K8s API — the served CRD version is discovered at runtime. Returns the
        family list envelope: items of {name, namespace, boot_order, member_count}
        plus returned/total/truncated (walked to completion) and served_version.
        ``boot_order`` mirrors spec.bootOrder: an ordered list of {members:
        [{kind, name}], power_on_delay?}. Older Supervisors without v1alpha4 return
        a teaching error naming the required version.

        Args:
            namespace: vSphere Namespace to list VM groups in (via list_namespaces).
            target: vCenter in config.yaml; omit for the default.
        """
        try:
            si = runtime._get_si(target)
            from vmware_vks.ops import vmservice as _vmsvc

            return _vmsvc.list_vm_groups(si, namespace)
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
    def list_vm_service_network_interfaces(
        namespace: str, vm_name: str, target: Optional[str] = None
    ) -> dict:
        """[READ] List the network interfaces (multi-NIC) of one VirtualMachine.

        Reads spec.network.interfaces[] off a single VM Service VirtualMachine
        (vmoperator.vmware.com) via the Supervisor K8s API; the served CRD version
        is discovered at runtime. Returns the family list envelope: items of {name,
        network_name, network_kind, network_api_version} plus
        returned/total/truncated and vm_name/served_version. A VM with no network
        block returns an empty list, not an error. Run list_namespaces for the
        namespace; the VM name comes from your own VM inventory in that namespace.

        Args:
            namespace: vSphere Namespace the VM lives in (via list_namespaces).
            vm_name: Name of the VirtualMachine to read interfaces from.
            target: vCenter in config.yaml; omit for the default.
        """
        try:
            si = runtime._get_si(target)
            from vmware_vks.ops import vmservice as _vmsvc

            return _vmsvc.list_vm_network_interfaces(si, namespace, vm_name)
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
    def get_access_info(
        tool: Optional[str] = None, target: Optional[str] = None
    ) -> dict:
        """[READ] Which account this server uses, and whether it holds each tool's vSphere privileges.

        Use this when a tool fails with a permission error, or before a write, to
        check what the configured account may do. Asks vCenter about the current
        session (needs only System.View, so any account can ask about itself).
        Returns target, account, the privilege states, and for one tool its
        requires/verdict/meaning; without tool, every tool grouped by verdict.
        Gotchas: privileges are checked on the root folder only, and a verdict of
        not_granted_at_root is NOT "no access" — it may be granted on a specific
        cluster. undetermined means vCenter did not answer; never read it as
        granted. Kubernetes calls (TKC, VM Service, kubeconfigs) are authorized by
        the namespace's view/edit/owner permission, which this cannot check, so
        those tools report undetermined. Namespace writes need Namespaces.Configure.

        Args:
            tool: One tool name on this server, e.g. "create_namespace". Omit to summarize every tool.
            target: vCenter in config.yaml; omit for the default.
        """
        unknown = runtime.unknown_tool_message(
            tool, runtime.REQUIREMENTS
        )  # no login for a misspelled name
        if unknown:
            return {"error": unknown}
        try:
            mgr = runtime._get_conn_mgr()
            name = mgr.resolve_target_name(target)
            si = mgr.connect(name)
            return runtime.vsphere_access_info(
                si, target=name, requirements=runtime.REQUIREMENTS, tool=tool
            )
        except Exception as e:
            return runtime._tool_error(e)

    return (
        issue_tkc_kubeconfig,
        get_harbor_info,
        list_namespace_storage_usage,
        list_vm_service_snapshots,
        list_vm_service_groups,
        list_vm_service_network_interfaces,
        get_access_info,
    )
