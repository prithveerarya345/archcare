"""`archcare clean`: find reclaimable space, and only remove it when asked.

Every task first *measures* (safe, no root needed) and returns how to apply itself.
Nothing is deleted unless the user passes --yes.
"""

import contextlib
import os
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from archcare import sh, system
from archcare.config import CleanCfg

UNITS = {"B": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


@dataclass
class Task:
    name: str
    summary: str
    reclaim: int | None  # bytes, None = unknown
    apply: Callable[[], bool] | None  # None = nothing to do

    @property
    def empty(self) -> bool:
        return self.apply is None


def parse_size(s: str) -> int:
    """'37.46 GiB' / '3.9G' / '500M' / '12 KiB' -> bytes."""
    m = re.search(r"([\d.]+)\s*([BKMGT])", s.upper())
    return int(float(m.group(1)) * UNITS[m.group(2)]) if m else 0


def du(p: Path) -> int:
    if p.is_symlink() or p.is_file():
        return p.lstat().st_size
    total = 0
    for root, _, files in os.walk(p, onerror=lambda e: None):
        for f in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(root, f)).st_size
    return total


def newest_mtime(p: Path) -> float:
    newest = p.lstat().st_mtime
    if p.is_dir() and not p.is_symlink():
        for root, dirs, files in os.walk(p, onerror=lambda e: None):
            for name in dirs + files:
                with contextlib.suppress(OSError):
                    newest = max(newest, os.lstat(os.path.join(root, name)).st_mtime)
    return newest


def remove(p: Path) -> None:
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p, ignore_errors=True)
    else:
        p.unlink(missing_ok=True)


# ---------------------------------------------------------------- tasks


def pacman_cache(cfg: CleanCfg) -> Task:
    keep = cfg.keep_package_versions
    old = sh.capture(["paccache", "-d", f"-k{keep}"])
    gone = sh.capture(["paccache", "-du", "-k0"])  # cached packages you've uninstalled
    if old.code == 127:
        return Task("pacman cache", "paccache not installed (pacman-contrib)", None, None)
    total = sum(parse_size(m) for m in re.findall(r"disk space saved: ([\d.]+ \w+)", old.out + gone.out))
    if not total:
        return Task("pacman cache", f"already trimmed to {keep} versions", 0, None)
    return Task(
        "pacman cache",
        f"old versions beyond the newest {keep}, plus uninstalled packages",
        total,
        lambda: (
            sh.interactive(["sudo", "paccache", "-r", f"-k{keep}"]) == 0
            and sh.interactive(["sudo", "paccache", "-ru", "-k0"]) == 0
        ),
    )


def yay_cache(home: Path) -> Task:
    root = home / ".cache" / "yay"
    entries = list(root.iterdir()) if root.is_dir() else []
    size = sum(du(e) for e in entries)
    if not size:
        return Task("yay build cache", "empty", 0, None)

    def apply() -> bool:
        for e in entries:
            remove(e)
        return True

    return Task("yay build cache", f"{len(entries)} AUR build folders", size, apply)


def orphans() -> Task:
    pkgs = system.orphans()
    if not pkgs:
        return Task("orphan packages", "none", 0, None)
    preview = ", ".join(pkgs[:6]) + (f" +{len(pkgs) - 6} more" if len(pkgs) > 6 else "")
    # pacman shows the list and asks before removing; -Rns also drops their configs and deps
    return Task(
        "orphan packages",
        f"{len(pkgs)}: {preview}",
        None,
        lambda: sh.interactive(["sudo", "pacman", "-Rns", *pkgs]) == 0,
    )


def trash(home: Path, cfg: CleanCfg, now: float | None = None) -> Task:
    """Items whose DeletionDate is older than trash_days (the date you trashed them, not file age)."""
    base = home / ".local/share/Trash"
    cutoff = (now or time.time()) - cfg.trash_days * 86400
    old: list[tuple[Path, Path]] = []
    for info in (base / "info").glob("*.trashinfo"):
        m = re.search(r"^DeletionDate=(.+)$", info.read_text(errors="replace"), re.M)
        try:
            deleted = datetime.fromisoformat(m.group(1).strip()).timestamp() if m else 0
        except ValueError:
            deleted = 0
        if deleted < cutoff:
            old.append((info, base / "files" / info.name.removesuffix(".trashinfo")))
    size = sum(du(f) for _, f in old if f.exists() or f.is_symlink())
    if not old:
        return Task("trash", f"nothing older than {cfg.trash_days} days", 0, None)

    def apply() -> bool:
        for info, f in old:
            remove(f)
            info.unlink(missing_ok=True)
        return True

    return Task("trash", f"{len(old)} items trashed over {cfg.trash_days} days ago", size, apply)


def user_cache(home: Path, cfg: CleanCfg, now: float | None = None) -> Task:
    root = home / ".cache"
    cutoff = (now or time.time()) - cfg.cache_days * 86400
    stale = [e for e in (root.iterdir() if root.is_dir() else []) if e.name != "yay" and newest_mtime(e) < cutoff]
    size = sum(du(e) for e in stale)
    if not stale:
        return Task("~/.cache", f"nothing untouched for {cfg.cache_days} days", 0, None)

    def apply() -> bool:
        for e in stale:
            remove(e)
        return True

    names = ", ".join(sorted(e.name for e in stale)[:5]) + ("…" if len(stale) > 5 else "")
    return Task("~/.cache", f"{len(stale)} folders untouched {cfg.cache_days}+ days ({names})", size, apply)


def journal(cfg: CleanCfg) -> Task:
    r = sh.capture(["journalctl", "--disk-usage"])
    m = re.search(r"take up ([\d.]+\s*[BKMGT])", r.out + r.err)
    used, limit = (parse_size(m.group(1)) if m else 0), parse_size(cfg.journal_max)
    if used <= limit:
        return Task("systemd journal", f"under {cfg.journal_max}", 0, None)
    return Task(
        "systemd journal",
        f"shrink from {m.group(1) if m else '?'} to {cfg.journal_max}",
        used - limit,
        lambda: sh.interactive(["sudo", "journalctl", f"--vacuum-size={cfg.journal_max}"]) == 0,
    )


def docker() -> Task:
    if not sh.have("docker") or not sh.capture(["docker", "info"], timeout=10).ok:
        return Task("docker", "not running", None, None)
    ids = sh.capture(["docker", "images", "-f", "dangling=true", "-q"]).out.split()
    if not ids:
        return Task("docker", "no dangling images", 0, None)
    return Task(
        "docker",
        f"{len(ids)} dangling images (untagged leftovers of rebuilds)",
        None,
        lambda: sh.interactive(["docker", "image", "prune", "-f"]) == 0,
    )


def downloads(home: Path, cfg: CleanCfg, now: float | None = None) -> Task:
    """Doesn't delete anything: files older than downloads_days move to Downloads/archive/YYYY-MM/."""
    root = home / "Downloads"
    cutoff = (now or time.time()) - cfg.downloads_days * 86400
    old = [
        e
        for e in (root.iterdir() if root.is_dir() else [])
        if e.name != "archive" and not e.name.startswith(".") and e.lstat().st_mtime < cutoff
    ]
    if not old:
        return Task("Downloads", f"nothing older than {cfg.downloads_days} days", 0, None)

    def apply() -> bool:
        for e in old:
            month = time.strftime("%Y-%m", time.localtime(e.lstat().st_mtime))
            dest = root / "archive" / month
            dest.mkdir(parents=True, exist_ok=True)
            target = dest / e.name
            if target.exists():
                target = dest / f"{e.stem}-{int(e.lstat().st_mtime)}{e.suffix}"
            e.rename(target)
        return True

    return Task("Downloads", f"sort {len(old)} items older than {cfg.downloads_days} days into archive/", 0, apply)


def all_tasks(home: Path, cfg: CleanCfg) -> dict[str, Callable[[], Task]]:
    """Lazy so --only doesn't pay for measuring everything."""
    return {
        "pacman": lambda: pacman_cache(cfg),
        "yay": lambda: yay_cache(home),
        "orphans": orphans,
        "trash": lambda: trash(home, cfg),
        "cache": lambda: user_cache(home, cfg),
        "journal": lambda: journal(cfg),
        "docker": docker,
        "downloads": lambda: downloads(home, cfg),
    }
