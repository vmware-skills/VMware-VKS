"""Readiness across Cluster API status contracts; missing is unknown."""


def readiness(status: dict, condition: str, legacy_key: str) -> bool | None:
    for item in status.get("conditions") or []:
        if item.get("type") == condition:
            return {"True": True, "False": False}.get(item.get("status"))
    value = status.get(legacy_key)
    return value if isinstance(value, bool) else None
