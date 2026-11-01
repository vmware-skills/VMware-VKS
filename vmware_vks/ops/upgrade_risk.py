"""Read-only pre-upgrade report for the vCenter 9.1.0/9.1.1 VUMDB advisory.

The vSphere APIs cannot inspect the affected database schema. This report
therefore never grants upgrade clearance; only Broadcom's checker or VDT can
establish whether the schema needs repair.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from vmware_policy import sanitize

from vmware_vks.ops.supervisor import _rest_get

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance


ADVISORY = (
    "https://blogs.vmware.com/cloud-foundation/2026/10/09/critical-advisory-"
    "pre-upgrade-verification-required-for-vsphere-supervisor-and-vks-"
    "environments-upgrading-to-vcenter-9-1-0-or-9-1-1/"
)
CHECKER = "https://knowledge.broadcom.com/external/article/455737"
TARGET_VERSIONS = ("9.1.0", "9.1.1")


def _source_scope(version: str) -> str:
    """Return in_scope, out_of_scope or unknown for the published source matrix."""
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:\.\d+)?", version)
    if not match:
        return "unknown"
    major, minor, update = map(int, match.groups()[:3])
    if (major, minor) == (8, 0):
        return "in_scope" if update >= 3 else "out_of_scope"
    if (major, minor) == (9, 0):
        return "in_scope" if update <= 2 else "unknown"
    return "out_of_scope"


def get_supervisor_upgrade_risk(
    si: ServiceInstance, target_version: str = "9.1.1"
) -> dict:
    """Report advisory applicability without making an unsupported DB claim."""
    if target_version not in TARGET_VERSIONS:
        raise ValueError("target_version must be 9.1.0 or 9.1.1")

    about = si.content.about
    source_version = str(about.version)
    source_scope = _source_scope(source_version)
    clusters: list[dict] | None = None
    try:
        raw = _rest_get(si, "/vcenter/namespace-management/clusters")
        if isinstance(raw, list) and all(isinstance(item, dict) for item in raw):
            clusters = [
                {
                    "cluster": sanitize(str(item.get("cluster") or ""), 100),
                    "status": sanitize(str(item.get("config_status") or ""), 60),
                }
                for item in raw
            ]
    except Exception:  # API unavailable/unauthorized means unknown, not absent
        pass

    supervisor_presence = (
        "unknown" if clusters is None else "present" if clusters else "not_detected"
    )
    if source_scope == "in_scope":
        applicability = "in_scope" if clusters else "unknown" if clusters is None else "no_supervisor_detected"
    else:
        applicability = source_scope

    if applicability == "in_scope":
        next_step = (
            "Do not start the upgrade. Run the official VUMDB checker or VDT on "
            "the source vCenter; fix and recheck if required, then back up "
            "vCenter and Supervisor before upgrading."
        )
    elif applicability == "no_supervisor_detected":
        next_step = (
            "Confirm that no Supervisor, VKS cluster or Foundation Load Balancer "
            "depends on this vCenter. If any does, run the official VUMDB checker "
            "or VDT before upgrading."
        )
    elif applicability == "unknown":
        next_step = (
            "Confirm the source version and Supervisor inventory with an account "
            "that can read namespace-management clusters. Run the official "
            "VUMDB checker or VDT if this advisory applies."
        )
    else:
        next_step = (
            "This source version is outside the advisory's published matrix. "
            "Follow the normal vCenter and Supervisor upgrade checks."
        )

    return {
        "source_version": sanitize(source_version, 60),
        "source_build": sanitize(str(about.build), 60),
        "target_version": target_version,
        "advisory_applicability": applicability,
        "supervisor_presence": supervisor_presence,
        "supervisors": clusters,
        "vumdb_schema_status": "unmeasured",
        "upgrade_clearance": "not_established",
        "next_step": next_step,
        "references": {"advisory": ADVISORY, "official_checker": CHECKER},
    }
