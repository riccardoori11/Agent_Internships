#!/usr/bin/env python3
"""Generate, install, and verify a persistent systemd user timer."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from zoneinfo import ZoneInfo

PROJECT = Path(__file__).resolve().parent.parent
TIMER = "internship-finder.timer"
CALENDAR = "*-*-* 08:00:00 Europe/Paris"


def systemd_quote(path: Path) -> str:
    """Quote systemd syntax and escape specifier and environment substitutions."""
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'


def generate(project: Path = PROJECT) -> Path:
    generated = project / "deploy" / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    template = (project / "deploy" / "internship-finder.service.in").read_text()
    # WorkingDirectory does not expand environment variables, unlike ExecStart.
    directory = systemd_quote(project).replace("$$", "$")
    service = template.replace("@PROJECT@", directory).replace("@RUNNER@", systemd_quote(project / "scripts" / "run-daily.sh"))
    (generated / "internship-finder.service").write_text(service)
    shutil.copyfile(project / "deploy" / TIMER, generated / TIMER)
    return generated


def command(args: list[str]) -> str:
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if result.returncode:
        raise RuntimeError(f"{' '.join(args)}: {(result.stderr or result.stdout).strip()}")
    return result.stdout.strip()


def verify() -> dict:
    enabled = command(["systemctl", "--user", "is-enabled", TIMER])
    active = command(["systemctl", "--user", "is-active", TIMER])
    properties = command(["systemctl", "--user", "show", TIMER, "--property=NextElapseUSecRealtime,LastTriggerUSec,TimersCalendar,Persistent,AccuracyUSec,RandomizedDelayUSec,FragmentPath"])
    effective = command(["systemctl", "--user", "cat", TIMER])
    if enabled != "enabled" or active != "active":
        raise RuntimeError(f"Timer is not enabled and active: enabled={enabled!r}, active={active!r}")
    if "OnCalendar=" + CALENDAR not in effective or "Persistent=true" not in effective:
        raise RuntimeError("Installed timer lacks the required Paris calendar or persistent catch-up setting")
    linger = command(["loginctl", "show-user", str(os.getuid()), "--property=Linger", "--value"])
    if linger != "yes":
        raise RuntimeError("Timer exists, but user lingering is disabled; enable it to run after logout: loginctl enable-linger " + command(["id", "-un"]))
    return {"active": True, "enabled": enabled, "state": active, "linger": linger, "properties": properties}


def record(status: dict) -> None:
    status["checked_at"] = datetime.now(timezone.utc).isoformat()
    status["timezone"] = "Europe/Paris"
    status["calendar"] = CALENDAR
    path = PROJECT / "deploy" / "schedule-status.json"
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(status, indent=2) + "\n")
    temp.replace(path)
    print(json.dumps(status, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["generate", "install", "verify"])
    parser.add_argument("--unit-dir", type=Path, help="Override the user unit install directory (default: $XDG_CONFIG_HOME/systemd/user)")
    options = parser.parse_args()
    ZoneInfo("Europe/Paris")  # Fail immediately if IANA timezone data is missing.
    generated = generate()
    if options.action == "generate":
        print(generated)
        return 0
    try:
        if options.action == "install":
            # Test service-manager connectivity before writing into the user's config.
            command(["systemctl", "--user", "show-environment"])
            user_config = options.unit_dir or Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "systemd" / "user"
            user_config.mkdir(parents=True, exist_ok=True)
            for name in ["internship-finder.service", TIMER]:
                shutil.copyfile(generated / name, user_config / name)
            command(["systemctl", "--user", "daemon-reload"])
            command(["systemctl", "--user", "enable", "--now", TIMER])
        record(verify())
        return 0
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        record({"active": False, "error": str(error), "generated_units": str(generated),
                "remaining_steps": ["Run on the normal Linux host outside the agent sandbox: loginctl enable-linger \"$(id -un)\"", f"cd {PROJECT} && ./scripts/install-schedule.sh", "./scripts/verify-schedule.sh"]})
        return 1


if __name__ == "__main__":
    sys.exit(main())
