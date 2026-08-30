"""The only repo-specific facts in this capability suite.

Every ``test_*.py`` file in this directory is identical across the family repos;
they differ only through this module. Keeping the difference in one small file is
what makes a rubric change portable — edit the eval once, copy it, and the scores
stay comparable between skills.
"""

from __future__ import annotations

#: Import path of the Python package under test.
PACKAGE = "vmware_vks"

#: Module holding the FastMCP server.
SERVER_MODULE = "vmware_vks.mcp_server.server"

#: CLI entry point name, used when scoring whether an error names something
#: concrete for the operator to run.
CLI_NAME = "vmware-vks"

#: Companion skills this one legitimately routes to. A required entity name that
#: this surface cannot produce is not a dead end *if* the description says which
#: sibling skill produces it — that is a documented hand-off rather than a gap.
COMPANION_SKILLS = (
    "vmware-aiops",
    "vmware-monitor",
    "vmware-storage",
    "vmware-vks",
    "vmware-nsx",
    "vmware-nsx-security",
    "vmware-aria",
    "vmware-avi",
    "vmware-harden",
    "vmware-pilot",
)

#: Entity tokens this skill's tools name, mapped to the words its listing tools
#: use. Authored from the registry's own required parameters, not from the
#: domain in the abstract. Drives ``test_entity_reachability``: a stem that is
#: not here is invisible to that eval, which is why the suite asserts coverage.
#: ``namespace`` carries no ``_name``/``_id`` suffix but is a lookup on nine
#: tools, so it is matched whole. Same for ``vm_class`` and
#: ``storage_policy``, both of which have real listing tools.
ENTITY_WORDS = {
    'cluster': ('cluster', 'clusters', 'tkc', 'supervisor'),
    'namespace': ('namespace', 'namespaces'),
    'vm_class': ('vm_class', 'vm_classes'),
    'storage_policy': ('storage_policy', 'storage_policies'),
    'version': ('version', 'versions', 'k8s_version'),
    # An entity, and deliberately not in NOT_AN_ENTITY: this skill asks for a VM
    # by name and ships no tool that lists VMs, so the operator must bring the
    # name from vmware-aiops or the vSphere client. Classifying it as
    # operator-supplied — the `worker_count` category — would hide that gap
    # behind a definition instead of scoring it. Reachability is meant to be
    # low here until a listing exists.
    'vm': ('vm', 'vms', 'vm_name'),
}

#: Skill-specific parameters that end in an entity suffix but are supplied by the
#: operator rather than discovered from an API. Universal exclusions (``target``,
#: paths, filters) live in the eval itself.
NOT_AN_ENTITY = frozenset(
    {
        # Chosen by the operator when scaling, not discovered.
        'worker_count',
    }
)

def get_server(module):
    """Return the FastMCP instance ``SERVER_MODULE`` exposes.

    The family has two shapes: a module-level ``mcp`` built at import time, and a
    ``build_server()`` factory (vmware-harden, vmware-debug). Declared per skill
    rather than probed with a try/except chain — a fallback would let a server
    that stops exposing what this file says silently resolve to the other shape,
    and the suite would go on scoring something nobody meant to measure.
    """
    return module.mcp
