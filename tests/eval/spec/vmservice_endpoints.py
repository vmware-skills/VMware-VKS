"""VERIFIED Supervisor VM Service CRDs (vmoperator.vmware.com).

Source: VCF 9.1 verified-endpoints spec, section D (Supervisor container/VM
Service). Every path the ops layer touches must be pinned here — this is the
anti-phantom-endpoint guard (CLAUDE.md 踩坑 #36): a prior skill shipped
hallucinated REST paths, half of which 404'd. These are Supervisor Kubernetes
CRDs reached through the kubernetes client (CustomObjectsApi), NOT vCenter REST
and NOT pyVmomi.

Served CRD version is DISCOVERED at runtime (K8s discovery API); this module
only bounds what versions/plurals the code is allowed to ask for.
"""

# The single API group for all VM Service CRDs. Anything else is a phantom.
VMOP_GROUP = "vmoperator.vmware.com"

# Served versions vmoperator ships on current Supervisors, newest-first.
# VirtualMachineSnapshot is NEW at v1alpha5; VirtualMachineGroup at v1alpha4.
# The exact served version is resolved at runtime — this only bounds the set.
VMOP_VERSIONS = (
    "v1alpha6",
    "v1alpha5",
    "v1alpha4",
    "v1alpha3",
    "v1alpha2",
    "v1alpha1",
)

# plural -> {kind, min served version}. VERIFIED in spec section D.
#   VirtualMachine          — spec.network.interfaces[] (multi-NIC readout)
#   VirtualMachineSnapshot  — kind new at v1alpha5
#   VirtualMachineGroup     — kind new at v1alpha4 (+ spec.bootOrder)
RESOURCES = {
    "virtualmachines": {"kind": "VirtualMachine", "min_version": "v1alpha1"},
    "virtualmachinesnapshots": {
        "kind": "VirtualMachineSnapshot",
        "min_version": "v1alpha5",
    },
    "virtualmachinegroups": {
        "kind": "VirtualMachineGroup",
        "min_version": "v1alpha4",
    },
}

ALLOWED_PLURALS = frozenset(RESOURCES)

# Explicitly NOT available as a dedicated CRD (spec section D): "Container
# Service" runs standard K8s Deployments as vSphere Pods via the VCF Automation
# UI/LCI — there is no vmoperator CRD to list, so no tool is built for it.
NO_CRD_FEATURES = ("container-service",)
