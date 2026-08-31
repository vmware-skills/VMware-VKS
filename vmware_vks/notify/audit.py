"""Audit logger — writes JSON Lines to ~/.vmware-vks/audit.log."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

_AUDIT_DIR = Path.home() / ".vmware-vks"
_AUDIT_FILE = _AUDIT_DIR / "audit.log"
_log = logging.getLogger("vmware-vks.audit")


class AuditLogger:
    def __init__(self, log_file: Path = _AUDIT_FILE) -> None:
        self._file = log_file
        self._file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # mkdir mode is masked by umask; enforce 0700 explicitly. Best-effort.
        try:
            os.chmod(self._file.parent, 0o700)
        except OSError:
            pass

    def log(
        self,
        target: str,
        operation: str,
        resource: str,
        parameters: dict,
        result: str,
        user: str = "",
    ) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "target": target,
            "operation": operation,
            "resource": resource,
            "parameters": parameters,
            "result": result,
            "user": user,
        }
        try:
            existed = self._file.exists()
            with open(self._file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            if not existed:
                # Restrict the log (operation history) to owner-only on creation.
                try:
                    os.chmod(self._file, 0o600)
                except OSError:
                    pass
        except OSError as e:
            _log.warning("Failed to write audit log: %s", e)
