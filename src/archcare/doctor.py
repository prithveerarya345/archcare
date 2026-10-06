"""`archcare doctor`: one health report. Each check is independent and read-only."""

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from archcare import sh, state, system
from archcare.config import Config
from archcare.paths import Paths

OK, INFO, WARN, FAIL = "ok", "info", "warn", "fail"
RANK = {OK: 0, INFO: 0, WARN: 1, FAIL: 2}


@dataclass
class Check:
    name: str
    level: str
    detail: str
    fix: str = ""


def _ago(ts: float | None) -> str:
    if not ts:
        return "never"
    days = (time.time() - ts) / 86400
    return f"{days * 24:.0f}h ago" if days < 1 else f"{days:.0f}d ago"


def failed_units() -> Check:
    units = system.failed_units() + [f"{u} (user)" for u in system.failed_units(user=True)]
    if not units:
        return Check("failed services", OK, "none")
    return Check("failed services", FAIL, ", ".join(units), "archcare explain")


def journal_errors() -> Check:
    errs = system.journal_errors()
    # count distinct sources, not lines: one chatty driver shouldn't look like 300 problems
    sources = {m.group(1) for line in errs if (m := re.search(r"\s(\S+?)(?:\[\d+\])?:", line))}
    if not errs:
        return Check("errors since boot", OK, "none")
    level = WARN if len(errs) > 20 else INFO
    return Check("errors since boot", level, f"{len(errs)} lines from {len(sources)} sources", "archcare explain")


def disk(cfg: Config) -> Check:
    d = system.disk()
    level = FAIL if d.used_percent >= 95 else WARN if d.used_percent >= cfg.alerts.disk_warn_percent else OK
    return Check("disk /", level, f"{d.used_percent:.0f}% used, {d.free / 1024**3:.0f} GB free", "archcare clean")


def smart() -> Check:
    if not sh.have("smartctl"):
        return Check("SSD health", INFO, "smartctl not installed", "sudo pacman -S smartmontools")
    dev = sh.capture(["findmnt", "-no", "SOURCE", "/"]).out.strip()
    disk_dev = re.sub(r"p?\d+$", "", dev) if dev.startswith("/dev/") else ""
    if not disk_dev:
        return Check("SSD health", INFO, f"can't map {dev or '/'} to a disk")
    r = sh.capture(["sudo", "-n", "smartctl", "-H", "-A", "-j", disk_dev])
    if r.code in (1, 127) or not r.out.strip():
        return Check("SSD health", INFO, "needs root", f"sudo smartctl -H {disk_dev}")
    try:
        data = json.loads(r.out)
    except ValueError:
        return Check("SSD health", INFO, "unreadable smartctl output")
    passed = data.get("smart_status", {}).get("passed")
    wear = data.get("nvme_smart_health_information_log", {}).get("percentage_used")
    detail = ("PASSED" if passed else "FAILING") + (f", {wear}% of rated life used" if wear is not None else "")
    level = FAIL if passed is False else WARN if (wear or 0) >= 80 else OK
    return Check("SSD health", level, detail, "back up now and replace the drive" if level == FAIL else "")


def battery() -> Check:
    b = system.battery()
    if not b:
        return Check("battery", INFO, "no battery")
    parts = [f"{b.health}% of design capacity" if b.health else "health unknown"]
    if b.cycles:
        parts.append(f"{b.cycles} cycles")
    if b.charge_limit and b.charge_limit < 100:
        parts.append(f"charging capped at {b.charge_limit}%")
    level = WARN if b.health and b.health < 70 else INFO if b.charge_limit == 100 else OK
    fix = ""
    if b.charge_limit == 100:
        knob = f"/sys/class/power_supply/{b.name}/charge_control_end_threshold"
        fix = f"cap charging at 80% to slow wear: echo 80 | sudo tee {knob}"
    return Check("battery", level, ", ".join(parts), fix)


def last_update() -> Check:
    when = system.last_full_upgrade()
    if not when:
        return Check("last full upgrade", WARN, "unknown", "archcare update")
    days = (time.time() - when.timestamp()) / 86400
    level = WARN if days > 14 else OK
    return Check("last full upgrade", level, f"{days:.0f} days ago", "archcare update" if level == WARN else "")


def reboot() -> Check:
    if system.reboot_needed():
        return Check("kernel", WARN, "updated since boot: running kernel's modules are gone", "reboot")
    return Check("kernel", OK, "running the installed kernel")


def mirrors() -> Check:
    age = system.mirrorlist_age_days()
    if age is None:
        return Check("mirrorlist", INFO, "unknown age")
    level = WARN if age > 60 else OK
    return Check("mirrorlist", level, f"updated {age:.0f} days ago", "sudo systemctl enable --now reflector.timer")


def pacnew() -> Check:
    files = system.pacnew_files()
    if not files:
        return Check(".pacnew files", OK, "none")
    critical = [f for f in files if f.name.startswith(("sudoers", "pacman.conf", "passwd", "shadow", "fstab"))]
    level = WARN if critical else INFO
    names = ", ".join(f.name for f in (critical or files)[:4])
    return Check(".pacnew files", level, f"{len(files)} unmerged ({names}…)", "archcare pacnew")


def orphans() -> Check:
    n = len(system.orphans())
    return Check(
        "orphan packages", INFO if n else OK, f"{n}" if n else "none", "archcare clean --only orphans" if n else ""
    )


def backups(paths: Paths, cfg: Config) -> Check:
    if not cfg.backup.repository:
        return Check(
            "backups", FAIL, "not configured", "set [backup] repository in config.toml, then archcare backup init"
        )
    last = state.load(paths).get("last_backup")
    level = FAIL if not last else WARN if time.time() - last > 3 * 86400 else OK
    return Check("backups", level, f"last {_ago(last)}", "archcare backup" if level != OK else "")


def dotfiles(paths: Paths) -> Check:
    repo = paths.dots_repo
    if not (repo / ".git").exists():
        return Check("dotfile backup", WARN, "never synced", "archcare dots sync")
    r = sh.capture(["git", "-C", str(repo), "log", "-1", "--format=%ct"])
    ts = float(r.out.strip()) if r.ok and r.out.strip() else None
    return Check("dotfile backup", OK, f"last change committed {_ago(ts)}")


def timers() -> Check:
    want_system = ["fstrim.timer", "paccache.timer", "reflector.timer"]
    want_user = ["archcare-dots.timer", "archcare-daily.timer", "archcare-weekly.timer", "archcare-monthly.timer"]
    off = [t for t in want_system if not system.unit_enabled(t)]
    off += [t for t in want_user if not system.unit_enabled(t, user=True)]
    if not off:
        return Check("timers", OK, "all enabled")
    fix = []
    if any(t in want_system for t in off):
        fix.append("sudo systemctl enable --now " + " ".join(t for t in off if t in want_system))
    if any(t in want_user for t in off):
        fix.append("archcare install-timers")
    return Check("timers", WARN, "off: " + ", ".join(off), "; ".join(fix))


def all_checks(paths: Paths, cfg: Config) -> list[Callable[[], Check]]:
    return [
        failed_units,
        journal_errors,
        lambda: disk(cfg),
        smart,
        battery,
        last_update,
        reboot,
        mirrors,
        pacnew,
        orphans,
        lambda: backups(paths, cfg),
        lambda: dotfiles(paths),
        timers,
    ]


def run(paths: Paths, cfg: Config) -> list[Check]:
    results = []
    for check in all_checks(paths, cfg):
        try:
            results.append(check())
        except Exception as e:  # one broken check must not hide the rest of the report
            results.append(Check(getattr(check, "__name__", "check"), INFO, f"check crashed: {e}"))
    return results


def worst(results: list[Check]) -> str:
    return max((r.level for r in results), key=RANK.__getitem__, default=OK)
