"""Resolve 9.1 version evidence without confusing cluster and Supervisor IDs."""
from urllib.parse import quote

_MAX_SUPERVISORS = 20


def read_current_version(si, cluster_id, read):
    listing = read(si, "/vcenter/namespace-management/supervisors/summaries")
    items = listing.get("items") if isinstance(listing, dict) else None
    if not isinstance(items, list) or len(items) > _MAX_SUPERVISORS:
        raise ValueError("Supervisor discovery is unreadable or exceeds its bounded lookup scope")
    matches = []
    seen = set()
    for item in items:
        sid = item.get("supervisor") if isinstance(item, dict) else None
        if not isinstance(sid, str) or not sid or sid in seen:
            raise ValueError("Supervisor discovery did not provide distinct identifiers")
        seen.add(sid)
        topology = read(si, "/vcenter/namespace-management/supervisors/" + quote(sid, safe="") + "/topology")
        if not isinstance(topology, list) or any(
            not isinstance(zone, dict) or not isinstance(zone.get("clusters"), list)
            or not all(isinstance(c, str) for c in zone["clusters"])
            for zone in topology
        ):
            raise ValueError("Supervisor topology is unreadable")
        if any(cluster_id in zone["clusters"] for zone in topology):
            matches.append(sid)
    if len(matches) != 1:
        raise ValueError("Compute cluster did not resolve to exactly one Supervisor")
    software = read(si, "/vcenter/namespace-management/software/supervisors/upgrades/" + quote(matches[0], safe=""))
    version = software.get("current_version") if isinstance(software, dict) else None
    if not isinstance(version, str) or not version.strip():
        raise ValueError("Supervisor current version was not measured")
    return version
