"""The dotfiles repo: a plain git repo of *copies* of your tracked config.

Copies (not symlinks) mean archcare never changes how your system is set up:
delete the repo and nothing on your machine breaks. Git gives history for free,
so "what did my kwinrc look like last week" is `git log -p home/.config/kwinrc`.

Layout inside the repo:
    home/.bashrc, home/.config/nvim/...   <- tracked files, mirrored from ~
    etc/pacman.conf, ...                  <- /etc files you modified from package defaults
    INVENTORY.md                          <- generated list of everything tracked
"""

import filecmp
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from archcare.dots import secrets
from archcare.dots.manifest import Manifest
from archcare.dots.scan import Status, modified_etc_files, scan
from archcare.paths import Paths


@dataclass
class SyncResult:
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (path, reason)
    commit: str | None = None
    pushed: bool = False


def collect(paths: Paths, m: Manifest) -> tuple[dict[str, Path], list[tuple[str, str]]]:
    """Map repo-relative path -> source file for everything that should be backed up."""
    wanted: dict[str, Path] = {}
    skipped: list[tuple[str, str]] = []
    limit = m.max_file_kb * 1024

    def consider(src: Path, rel: str, excludes: list[str]) -> None:
        name = src.name
        if any(fnmatch(name, x) or fnmatch(rel, x) for x in excludes):
            return
        if m.is_secret(paths.tilde(src)):
            return
        if not src.exists():  # broken symlink
            skipped.append((rel, "broken symlink"))
            return
        if src.stat().st_size > limit:
            skipped.append((rel, f"bigger than {m.max_file_kb} KB"))
            return
        if secrets.is_binary(src):
            skipped.append((rel, "binary file"))
            return
        if found := secrets.scan(src):
            skipped.append((rel, "looks like it contains a secret: " + ", ".join(found)))
            return
        wanted[rel] = src

    for g in m.groups.values():
        excludes = m.exclude + g.exclude
        for tp in g.paths:
            root = paths.expand(tp)
            if m.is_secret(tp) or not root.exists():
                continue
            if root.is_file():
                consider(root, "home/" + root.relative_to(paths.home).as_posix(), excludes)
                continue
            for dirpath, dirs, files in os.walk(root):
                dirs[:] = sorted(d for d in dirs if not any(fnmatch(d, x) for x in excludes))
                for f in sorted(files):
                    src = Path(dirpath) / f
                    consider(src, "home/" + src.relative_to(paths.home).as_posix(), excludes)

    if m.backup_modified_etc:
        readable, unreadable = modified_etc_files()
        for p in readable:
            if os.access(p, os.R_OK):
                consider(p, "etc/" + p.relative_to("/etc").as_posix(), m.exclude)
            else:
                unreadable.append(p)
        skipped.extend(
            (f"etc/{p.relative_to('/etc')}", "needs root to check") for p in unreadable
        )
    return wanted, skipped


def sync(paths: Paths, m: Manifest, dry_run: bool = False, push: bool | None = None) -> SyncResult:
    repo = paths.dots_repo
    wanted, skipped = collect(paths, m)
    res = SyncResult(skipped=skipped)

    existing = {
        p.relative_to(repo).as_posix()
        for top in ("home", "etc")
        if (repo / top).exists()
        for p in (repo / top).rglob("*")
        if p.is_file() or p.is_symlink()
    }
    for rel, src in sorted(wanted.items()):
        dst = repo / rel
        if rel not in existing or not filecmp.cmp(src, dst, shallow=False):
            res.changed.append(rel)
    res.removed = sorted(existing - wanted.keys())

    if dry_run:
        return res

    ensure_repo(repo)
    for rel in res.changed:
        dst = repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(wanted[rel], dst)
    for rel in res.removed:
        (repo / rel).unlink()
    prune_empty_dirs(repo)
    (repo / "INVENTORY.md").write_text(inventory(paths, m, wanted, skipped))

    git(repo, "add", "-A")
    if git(repo, "status", "--porcelain").strip():
        res.commit = commit_message(res)
        git(repo, "commit", "-q", "-m", res.commit)
        if (m.push if push is None else push) and git(repo, "remote").strip():
            git(repo, "push", "-q")
            res.pushed = True
    return res


def restore(paths: Paths, m: Manifest, group: str | None, dry_run: bool) -> list[tuple[str, str]]:
    """Copy files from the repo back into ~. Anything overwritten is saved first.

    Returns (path, action) pairs. /etc is never written; it needs root and care.
    """
    repo = paths.dots_repo / "home"
    if not repo.exists():
        return []
    backup_dir = paths.state_dir / "restore-backups" / time.strftime("%Y%m%d-%H%M%S")
    actions: list[tuple[str, str]] = []
    for src in sorted(p for p in repo.rglob("*") if p.is_file()):
        target = paths.home / src.relative_to(repo)
        tilde = paths.tilde(target)
        if group and m.group_of(tilde) != group:
            continue
        if target.exists() and filecmp.cmp(src, target, shallow=False):
            continue
        actions.append((tilde, "overwrite" if target.exists() else "create"))
        if dry_run:
            continue
        if target.exists():
            saved = backup_dir / src.relative_to(repo)
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    return actions


def inventory(paths: Paths, m: Manifest, wanted: dict[str, Path], skipped: list[tuple[str, str]]) -> str:
    """Human-readable record of what is tracked, committed alongside the files."""
    entries = scan(paths, m, sizes=False)
    counts: dict[str, int] = {}
    for e in entries:
        counts[e.status] = counts.get(e.status, 0) + 1

    lines = [
        "# Dotfiles inventory",
        "",
        # no timestamp here: it would make every sync a commit; git log has the dates
        "Generated by `archcare dots sync`. Do not edit by hand.",
        "",
        "| Status | Entries |",
        "|---|---:|",
        *(f"| {s} | {counts.get(s, 0)} |" for s in Status),
        "",
    ]
    for g in m.groups.values():
        lines += [f"## {g.name}", "", "| Path | Files | Size |", "|---|---:|---:|"]
        for tp in g.paths:
            prefix = "home/" + tp.removeprefix("~/")
            files = [r for r in wanted if r == prefix or r.startswith(prefix + "/")]
            size = sum(wanted[r].stat().st_size for r in files)
            shown = tp if files else f"{tp} *(not found)*"
            lines.append(f"| `{shown}` | {len(files)} | {human(size)} |")
        lines.append("")
    etc = sorted(r for r in wanted if r.startswith("etc/"))
    if etc:
        lines += ["## /etc (modified from package defaults)", "", *(f"- `/{r}`" for r in etc), ""]
    if skipped:
        lines += ["## Skipped", "", *(f"- `{r}`: {why}" for r, why in skipped), ""]
    return "\n".join(lines)


def commit_message(res: SyncResult) -> str:
    n = len(res.changed) + len(res.removed)
    touched = res.changed + res.removed
    head = f"dots: {n} file{'s' * (n != 1)} changed"
    body = "\n".join(f"- {r}" for r in touched[:50])
    if len(touched) > 50:
        body += f"\n- ... and {len(touched) - 50} more"
    return f"{head}\n\n{body}" if body else head


def ensure_repo(repo: Path) -> None:
    if (repo / ".git").exists():
        return
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-q", "-b", "main")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def prune_empty_dirs(repo: Path) -> None:
    for top in ("home", "etc"):
        root = repo / top
        if not root.exists():
            continue
        for dirpath, _, _ in sorted(os.walk(root), key=lambda t: -len(t[0])):
            if not os.listdir(dirpath):
                os.rmdir(dirpath)


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
