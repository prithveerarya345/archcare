"""Read-only facts about the machine. Every function here is safe to call any time."""

import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from archcare import sh

PACMAN_LOG = Path("/var/log/pacman.log")
POWER = Path("/sys/class/power_supply")


# ---------------------------------------------------------------- disk


@dataclass
class Disk:
    total: int
    used: int
    free: int

    @property
    def used_percent(self) -> float:
        return 100 * self.used / self.total if self.total else 0.0

    @property
    def free_percent(self) -> float:
        return 100 - self.used_percent


def disk(path: str = "/") -> Disk:
    u = shutil.disk_usage(path)
    return Disk(u.total, u.used, u.free)


# ---------------------------------------------------------------- battery


@dataclass
class Battery:
    name: str
    capacity: int  # current charge %
    charging: bool  # on AC power
    health: float | None  # full / design, %
    cycles: int | None
    charge_limit: int | None  # charge_control_end_threshold, if the laptop supports it


def battery(root: Path = POWER) -> Battery | None:
    bats = sorted(root.glob("BAT*"))
    if not bats:
        return None
    b = bats[0]

    def read(name: str) -> str | None:
        try:
            return (b / name).read_text().strip()
        except OSError:
            return None

    def num(name: str) -> int | None:
        v = read(name)
        return int(v) if v and v.lstrip("-").isdigit() else None

    full = num("energy_full") or num("charge_full")
    design = num("energy_full_design") or num("charge_full_design")
    on_ac = any(
        (p / "online").exists() and (p / "online").read_text().strip() == "1"
        for p in root.iterdir()
        if not p.name.startswith("BAT")
    )
    return Battery(
        name=b.name,
        capacity=num("capacity") or 0,
        charging=on_ac or read("status") in ("Charging", "Full"),
        health=round(100 * full / design, 1) if full and design else None,
        cycles=num("cycle_count"),
        charge_limit=num("charge_control_end_threshold"),
    )


# ---------------------------------------------------------------- pacman


_TS = re.compile(r"^\[(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4})\]")


def last_full_upgrade(log: Path = PACMAN_LOG) -> datetime | None:
    """Time of the most recent `pacman -Syu`, straight from pacman's own log."""
    try:
        lines = log.read_text(errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if "starting full system upgrade" in line and (m := _TS.match(line)):
            return datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S%z")
    return None


def pending_updates() -> list[str] | None:
    """Repo packages with updates, via checkupdates (safe: uses a temp db copy). None = couldn't check."""
    r = sh.capture(["checkupdates"], timeout=120)
    if r.code == 2:  # checkupdates: 2 means "no updates"
        return []
    if not r.ok:
        return None
    return [line.split()[0] for line in r.out.splitlines() if line.strip()]


def orphans() -> list[str]:
    r = sh.capture(["pacman", "-Qdtq"])
    return r.out.split() if r.ok else []


def pacnew_files(root: Path = Path("/etc")) -> list[Path]:
    found = []
    for dirpath, _, files in os.walk(root, onerror=lambda e: None):
        found += [Path(dirpath) / f for f in files if f.endswith((".pacnew", ".pacsave"))]
    return sorted(found)


def mirrorlist_age_days(path: Path = Path("/etc/pacman.d/mirrorlist")) -> float | None:
    try:
        return (datetime.now().timestamp() - path.stat().st_mtime) / 86400
    except OSError:
        return None


# ---------------------------------------------------------------- kernel / python


def reboot_needed(modules: Path = Path("/usr/lib/modules")) -> bool:
    """On Arch a kernel upgrade deletes the running kernel's module folder."""
    return not (modules / os.uname().release).is_dir()


def stale_python_packages(lib: Path = Path("/usr/lib")) -> dict[str, list[str]]:
    """After a Python minor bump, packages still owning files in the old python3.X dir need a rebuild."""
    current = f"python3.{sys.version_info.minor}"
    system_py = sh.capture(["python3", "-c", "import sys; print(f'python3.{sys.version_info.minor}')"])
    if system_py.ok:
        current = system_py.out.strip()
    stale: dict[str, list[str]] = {}
    for d in sorted(lib.glob("python3.*")):
        if d.name == current or not d.is_dir():
            continue
        r = sh.capture(["pacman", "-Qoq", str(d)])
        if r.ok and r.out.strip():
            stale[d.name] = sorted(set(r.out.split()))
    return stale


# ---------------------------------------------------------------- systemd / journal


def failed_units(user: bool = False) -> list[str]:
    cmd = ["systemctl", "--failed", "--no-legend", "--plain"]
    if user:
        cmd.insert(1, "--user")
    r = sh.capture(cmd)
    return [line.split()[0] for line in r.out.splitlines() if line.strip()] if r.ok else []


def unit_enabled(unit: str, user: bool = False) -> bool:
    cmd = ["systemctl", "is-enabled", unit]
    if user:
        cmd.insert(1, "--user")
    return sh.capture(cmd).out.strip() == "enabled"


def journal_errors(boot: str = "0", limit: int = 300) -> list[str]:
    r = sh.capture(["journalctl", "-b", boot, "-p", "err", "-q", "--no-pager", "-o", "short-iso", "-n", str(limit)])
    return [line for line in r.out.splitlines() if line.strip()] if r.ok else []
