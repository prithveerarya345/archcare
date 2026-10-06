"""Inventory every dotfile on the machine and classify it against the manifest."""

import os
import shutil
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path

from archcare.dots.manifest import Manifest
from archcare.paths import Paths

# Containers we look *inside* instead of treating as one entry.
CONTAINERS = {".config", ".local", ".cache"}
LARGE_BYTES = 5 * 1024 * 1024


class Status(StrEnum):
    TRACKED = "tracked"
    PARTIAL = "partial"  # something inside is tracked, e.g. ~/.config/systemd
    UNTRACKED = "untracked"
    IGNORED = "ignored"
    SECRET = "secret"
    MISSING = "missing"  # in the manifest but no longer on disk


@dataclass
class Entry:
    path: str  # ~/ form
    status: Status
    group: str | None = None
    size: int = 0
    hint: str = ""


def candidates(paths: Paths) -> list[Path]:
    """Top-level dotfiles in ~ plus everything directly inside ~/.config."""
    out = [p for p in paths.home.iterdir() if p.name.startswith(".") and p.name not in CONTAINERS]
    config = paths.home / ".config"
    if config.is_dir():
        out.extend(config.iterdir())
    return sorted(out)


def classify(tilde: str, m: Manifest) -> tuple[Status, str | None]:
    # secret beats everything: a tracked path inside ~/.ssh still never leaves the machine
    if m.is_secret(tilde):
        return Status.SECRET, None
    if group := m.group_of(tilde):
        return Status.TRACKED, group
    if m.partly_tracked(tilde):
        return Status.PARTIAL, None
    if m.is_ignored(tilde):
        return Status.IGNORED, None
    return Status.UNTRACKED, None


def scan(paths: Paths, m: Manifest, sizes: bool = True) -> list[Entry]:
    entries: list[Entry] = []
    for p in candidates(paths):
        tilde = paths.tilde(p)
        status, group = classify(tilde, m)
        e = Entry(tilde, status, group)
        if sizes and status in (Status.TRACKED, Status.UNTRACKED, Status.PARTIAL):
            e.size = du(p)
        if status is Status.UNTRACKED:
            e.hint = hint_for(p, e.size)
        entries.append(e)

    on_disk = {e.path for e in entries}
    for g in m.groups.values():
        for tp in g.paths:
            if tp not in on_disk and not paths.expand(tp).exists():
                entries.append(Entry(tp, Status.MISSING, g.name))
    return entries


def du(p: Path) -> int:
    if p.is_symlink() or p.is_file():
        try:
            return p.lstat().st_size
        except OSError:
            return 0
    total = 0
    for root, dirs, files in os.walk(p):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            with suppress(OSError):
                total += os.lstat(os.path.join(root, f)).st_size
    return total


def hint_for(p: Path, size: int) -> str:
    if size > LARGE_BYTES:
        return "large: probably app data, consider ignore"
    # only folders: KDE alone writes dozens of *rc files whose names match no package
    name = app_name(p.name)
    if p.is_dir() and name and not shutil.which(name) and not related_package(name):
        return "no matching package: leftover?"
    return ""


def related_package(name: str) -> bool:
    """Loose match so 'baloofile' -> baloo and 'kde' -> kde-cli-tools count as installed."""
    pkgs = installed_packages()
    if not pkgs:  # not on Arch / pacman missing: don't guess
        return True
    return any(p == name or p.startswith(name) or (len(p) >= 4 and name.startswith(p)) for p in pkgs)


def app_name(filename: str) -> str:
    """'.vimrc' -> 'vim', 'kwinrc' -> 'kwin', 'starship.toml' -> 'starship'."""
    n = filename.lstrip(".").lower()
    for suffix in (".toml", ".json", ".conf", ".yaml", ".yml", ".list"):
        n = n.removesuffix(suffix)
    if n.endswith("rc") and len(n) > 3:
        n = n[:-2]
    return n


@cache
def installed_packages() -> frozenset[str]:
    try:
        out = subprocess.run(["pacman", "-Qq"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return frozenset()
    return frozenset(out.split())


def modified_etc_files() -> tuple[list[Path], list[Path]]:
    """/etc files that pacman says you changed: (readable, needs_root)."""
    try:
        out = subprocess.run(
            ["pacman", "-Qii"],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "LC_ALL": "C"},
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return [], []
    readable, unreadable = [], []
    for raw in out.splitlines():
        line = raw.removeprefix("Backup Files    :").strip()
        if line.endswith("[modified]"):
            readable.append(Path(line.removesuffix("[modified]").strip()))
        elif line.endswith("[unreadable]"):
            unreadable.append(Path(line.removesuffix("[unreadable]").strip()))
    return readable, unreadable
