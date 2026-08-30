"""Pre-flight diagnostics for vmware-vks."""

from __future__ import annotations

import logging
import stat
from pathlib import Path

from rich.console import Console
from rich.table import Table

_log = logging.getLogger("vmware-vks.doctor")
console = Console()


def run_doctor(config_path: Path | None = None) -> bool:
    """Run all pre-flight checks. Returns True if all pass."""
    from vmware_vks.config import CONFIG_FILE, ENV_FILE, load_config

    checks: list[tuple[str, bool, str]] = []

    # 1. Config file
    path = config_path or CONFIG_FILE
    if path.exists():
        checks.append(("Config file", True, str(path)))
    else:
        checks.append(
            (
                "Config file",
                False,
                f"Not found: {path}. Run 'vmware-vks init' "
                "(or copy config.example.yaml by hand).",
            )
        )

    # 1b. .env permissions
    #
    # Every other skill's doctor checked this; vmware-vks imported ENV_FILE and
    # never used it — the fingerprint of a check that was planned and dropped.
    # It is not decorative: config.py loads this file, so it holds the
    # per-target passwords, and CLAUDE.md requires it be chmod 600.
    if not ENV_FILE.exists():
        checks.append(
            (
                ".env file",
                False,
                f"Not found: {ENV_FILE} — passwords are read from here. "
                f"Create it, then: chmod 600 {ENV_FILE}",
            )
        )
    else:
        mode = ENV_FILE.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            checks.append(
                (
                    ".env file",
                    False,
                    f"Permissions too open ({oct(stat.S_IMODE(mode))}) — "
                    f"other users on this host can read your passwords. "
                    f"Run: chmod 600 {ENV_FILE}",
                )
            )
        else:
            checks.append((".env file", True, f"Found, permissions 600: {ENV_FILE}"))

    # 2. Load config
    config = None
    try:
        config = load_config(path)
        checks.append(("Config parse", True, f"{len(config.targets)} target(s)"))
    except Exception as e:
        checks.append(("Config parse", False, str(e)))

    # 3. Passwords
    if config:
        for t in config.targets:
            try:
                _ = t.password
                checks.append((f"Password ({t.name})", True, "Set"))
            except OSError as e:
                checks.append(
                    (
                        f"Password ({t.name})",
                        False,
                        f"{e} Run 'vmware-vks init' to set it (or add it to "
                        "~/.vmware-vks/.env by hand).",
                    )
                )

    # 4. vCenter reachable + version + WCP
    #
    # One try wrapped all three, and the single except labelled every failure
    # "vCenter reachable". On a real vCenter that printed the row twice — passing
    # with v8.0.3, then failing with a 401 from the Workload Management endpoint.
    # vCenter was reachable; the row above said so. A diagnostic that names the
    # wrong layer sends the operator to check networking and credentials that are
    # already fine. `stage` carries which check was in flight so the failure is
    # attributed to it.
    if config:
        for t in config.targets:
            stage = f"vCenter reachable ({t.name})"
            try:
                from vmware_vks.connection import ConnectionManager

                mgr = ConnectionManager(config)
                si = mgr.connect(t.name)
                version = si.content.about.version
                checks.append((f"vCenter reachable ({t.name})", True, f"v{version}"))

                stage = f"vCenter version ({t.name})"
                parts = tuple(int(x) for x in version.split(".")[:2])
                if parts >= (8, 0):
                    checks.append(
                        (f"vCenter version ({t.name})", True, f"{version} >= 8.0 ✓")
                    )
                else:
                    checks.append(
                        (
                            f"vCenter version ({t.name})",
                            False,
                            f"{version} < 8.0 (requires 8.x+)",
                        )
                    )

                stage = f"WCP enabled ({t.name})"
                from vmware_vks.ops.supervisor import _rest_get

                clusters = _rest_get(si, "/vcenter/namespace-management/clusters")
                running = [c for c in clusters if c.get("config_status") == "RUNNING"]
                if running:
                    checks.append(
                        (
                            f"WCP enabled ({t.name})",
                            True,
                            f"{len(running)} cluster(s) running",
                        )
                    )
                else:
                    checks.append(
                        (
                            f"WCP enabled ({t.name})",
                            False,
                            "No running Supervisor. Enable Workload Management in vCenter UI.",
                        )
                    )
            except Exception as e:
                checks.append((stage, False, str(e)))

    # Print table
    table = Table(title="vmware-vks Doctor", show_header=True)
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Detail")

    all_passed = True
    for name, passed, detail in checks:
        status = "[green]✓ PASS[/green]" if passed else "[red]✗ FAIL[/red]"
        table.add_row(name, status, detail)
        if not passed:
            all_passed = False

    console.print(table)
    return all_passed
