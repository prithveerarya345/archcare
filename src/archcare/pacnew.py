"""`.pacnew` handling: when pacman upgrades a config file you've edited, it writes the new
default next to yours as `<file>.pacnew` and keeps yours. Left unmerged, you miss new
options or fixes; merged carelessly, you can lock yourself out (sudoers, pam).
"""

import difflib
import os
import time
from dataclasses import dataclass
from pathlib import Path

from archcare import sh

VALIDATORS: dict[str, list[str]] = {
    # file -> command that must pass before a change is kept ({} = path)
    "/etc/sudoers": ["visudo", "-c", "-f", "{}"],
}


@dataclass
class Pending:
    new: Path  # the .pacnew / .pacsave file
    current: Path

    @property
    def kind(self) -> str:
        return self.new.suffix.lstrip(".")  # pacnew | pacsave

    @property
    def validator(self) -> list[str] | None:
        key = str(self.current)
        if key in VALIDATORS or key.startswith("/etc/sudoers.d/"):
            return [a.replace("{}", key) for a in VALIDATORS.get(key, VALIDATORS["/etc/sudoers"])]
        return None


def pending(files: list[Path]) -> list[Pending]:
    return [Pending(f, f.with_suffix("")) for f in files]


def read(path: Path) -> list[str] | None:
    """File lines, using sudo only if we must (sudoers is root-only). None = unreadable."""
    if os.access(path, os.R_OK):
        return path.read_text(errors="replace").splitlines(keepends=True)
    r = sh.capture(["sudo", "-n", "cat", str(path)])
    return r.out.splitlines(keepends=True) if r.ok else None


def diff(p: Pending) -> list[str] | None:
    a, b = read(p.current), read(p.new)
    if a is None or b is None:
        return None
    return list(difflib.unified_diff(a, b, str(p.current), str(p.new)))


def diffstat(lines: list[str]) -> tuple[int, int]:
    added = sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---"))
    return added, removed


# ---------------------------------------------------------------- actions (all go through sudo)


def keep_current(p: Pending) -> bool:
    return sh.interactive(["sudo", "rm", "--", str(p.new)]) == 0


def use_new(p: Pending) -> bool:
    """Replace with the package default; the old file is kept as <file>.archcare-<time>.bak."""
    backup = f"{p.current}.archcare-{time.strftime('%Y%m%d-%H%M%S')}.bak"
    if sh.interactive(["sudo", "cp", "-a", "--", str(p.current), backup]) != 0:
        return False
    if sh.interactive(["sudo", "mv", "--", str(p.new), str(p.current)]) != 0:
        return False
    return validate_or_restore(p, backup)


def merge(p: Pending, editor: str = "nvim") -> bool:
    """Open both in a diff editor as root; afterwards the user decides whether the .pacnew can go."""
    backup = f"{p.current}.archcare-{time.strftime('%Y%m%d-%H%M%S')}.bak"
    if sh.interactive(["sudo", "cp", "-a", "--", str(p.current), backup]) != 0:
        return False
    sh.interactive(["sudo", editor, "-d", str(p.current), str(p.new)])
    return validate_or_restore(p, backup)


def validate_or_restore(p: Pending, backup: str) -> bool:
    if not p.validator:
        return True
    if sh.interactive(["sudo", *p.validator]) == 0:
        return True
    # a broken sudoers locks you out of sudo: put the old one straight back
    sh.interactive(["sudo", "mv", "--", backup, str(p.current)])
    return False
