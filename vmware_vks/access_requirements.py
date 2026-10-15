"""vSphere privileges each MCP tool needs (HLD §17 D-17), for ``get_access_info``.

This skill makes almost no SOAP calls: pyVmomi opens the session and reads
``ServiceInstance.content.about`` (System.Anonymous, always granted). The work
goes through two other APIs whose checks pyVmomi's metadata cannot describe:

* the vSphere Automation REST API (``/api/vcenter/namespace-management/...``,
  ``/api/vcenter/namespaces/...``, ``/api/vcenter/storage/policies``). Reading
  inventory there is System.Read; storage policies are StorageProfile.View.
  Creating, changing or deleting a namespace is checked against
  Namespaces.Manage, which pyVmomi's metadata does not define; the family gate
  lists it as a reviewed extra (to verify against a live privilegeList).
* the Supervisor Kubernetes API (TKC, VM Service, kubeconfigs, storage usage),
  reached with a /wcp/login token. Kubernetes authorizes those calls with the
  namespace's own permissions (view / edit / owner), not with vSphere
  privileges on the root folder. Those tools are ``None``: always
  ``undetermined``, because a root-folder check cannot answer for them.

The family gate (scripts/lib/access_requirements_cover_tools.py) checks that the
keys are exactly the registered tools and that every id is one vSphere defines.
"""

from __future__ import annotations

_READ = ["System.Read"]

REQUIREMENTS: dict[str, list[str] | None] = {
    # REST: namespace-management clusters + software; SOAP: content.about.
    "check_vks_compatibility": list(_READ),
    "get_supervisor_status": list(_READ),
    "list_supervisor_storage_policies": ["System.Read", "StorageProfile.View"],
    "list_namespaces": list(_READ),
    "get_namespace": list(_READ),
    "create_namespace": ["System.Read", "Namespaces.Manage"],
    "update_namespace": ["System.Read", "Namespaces.Manage"],
    "delete_namespace": ["System.Read", "Namespaces.Manage"],
    "list_vm_classes": list(_READ),
    # Supervisor Kubernetes API: the namespace role decides (None); see above.
    "list_tkc_clusters": None,
    "get_tkc_cluster": None,
    "get_tkc_available_versions": None,
    "create_tkc_cluster": None,
    "scale_tkc_cluster": None,
    "upgrade_tkc_cluster": None,
    "delete_tkc_cluster": None,
    "issue_supervisor_kubeconfig": None,
    "issue_tkc_kubeconfig": None,
    "get_harbor_info": list(_READ),  # REST /vcenter/content/registries/harbor
    "list_namespace_storage_usage": None,
    "list_vm_service_snapshots": None,
    "list_vm_service_groups": None,
    "list_vm_service_network_interfaces": None,
    # AuthorizationManager.HasPrivilegeOnEntities / RetrieveAllPermissions /
    # roleList are all System.View.
    "get_access_info": ["System.View"],
}
