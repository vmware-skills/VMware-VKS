"""vSphere privileges each MCP tool needs (HLD §17 D-17), for ``get_access_info``.

This skill makes almost no SOAP calls: pyVmomi opens the session and reads
``ServiceInstance.content.about`` (System.Anonymous, always granted). The work
goes through two other APIs whose checks pyVmomi's metadata cannot describe:

* the vSphere Automation REST API (``/api/vcenter/namespace-management/...``,
  ``/api/vcenter/namespaces/...``, ``/api/vcenter/storage/policies``). Reading
  inventory there is System.Read; storage policies are StorageProfile.View.
  Creating, changing or deleting a namespace is checked against the
  Namespaces.* privileges, which pyVmomi's metadata does not define, so the
  family gate cannot accept them yet (reported, not added) — for the three
  namespace writes the verdict below covers only the read half.
* the Supervisor Kubernetes API (TKC, VM Service, kubeconfigs, storage usage),
  reached with a /wcp/login token. Kubernetes authorizes those calls with the
  namespace's own permissions (view / edit / owner), not with vSphere
  privileges on the root folder; only the endpoint lookup (System.Read) is a
  vSphere privilege. A granted verdict here does not mean the namespace role
  allows the call.

The family gate (scripts/lib/access_requirements_cover_tools.py) checks that the
keys are exactly the registered tools and that every id is one vSphere defines.
"""

from __future__ import annotations

_READ = ["System.Read"]

REQUIREMENTS: dict[str, list[str]] = {
    # REST: namespace-management clusters + software; SOAP: content.about.
    "check_vks_compatibility": list(_READ),
    "get_supervisor_status": list(_READ),
    "list_supervisor_storage_policies": ["System.Read", "StorageProfile.View"],
    "list_namespaces": list(_READ),
    "get_namespace": list(_READ),
    # Namespaces.Manage is also checked; not in pyVmomi's metadata (see above).
    "create_namespace": list(_READ),
    "update_namespace": list(_READ),
    "delete_namespace": list(_READ),
    "list_vm_classes": list(_READ),
    # Supervisor Kubernetes API: the namespace role decides; see above.
    "list_tkc_clusters": list(_READ),
    "get_tkc_cluster": list(_READ),
    "get_tkc_available_versions": list(_READ),
    "create_tkc_cluster": list(_READ),
    "scale_tkc_cluster": list(_READ),
    "upgrade_tkc_cluster": list(_READ),
    "delete_tkc_cluster": list(_READ),
    "issue_supervisor_kubeconfig": list(_READ),
    "issue_tkc_kubeconfig": list(_READ),
    "get_harbor_info": list(_READ),  # REST /vcenter/content/registries/harbor
    "list_namespace_storage_usage": list(_READ),
    "list_vm_service_snapshots": list(_READ),
    "list_vm_service_groups": list(_READ),
    "list_vm_service_network_interfaces": list(_READ),
    # AuthorizationManager.HasPrivilegeOnEntities / RetrieveAllPermissions /
    # roleList are all System.View.
    "get_access_info": ["System.View"],
}
