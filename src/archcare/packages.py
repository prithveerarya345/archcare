"""Package lists: what's installed, saved as plain text so a fresh install can be rebuilt."""

from pathlib import Path

from archcare import sh

LISTS = {
    "native.txt": ["pacman", "-Qqen"],  # explicitly installed, from the repos
    "aur.txt": ["pacman", "-Qqem"],  # explicitly installed, foreign (AUR)
}


def export(dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for name, cmd in LISTS.items():
        r = sh.capture(cmd)
        if r.ok:
            (dest / name).write_text(r.out)
            written.append(dest / name)
    if sh.have("snap"):
        r = sh.capture(["snap", "list"])
        if r.ok:
            snaps = [ln.split()[0] for ln in r.out.splitlines()[1:] if ln.strip()]
            (dest / "snap.txt").write_text("\n".join(snaps) + "\n")
            written.append(dest / "snap.txt")
    return written


def read_list(path: Path) -> list[str]:
    try:
        return [ln.strip() for ln in path.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    except OSError:
        return []


def installable_from_repos(pkgs: list[str]) -> tuple[list[str], list[str]]:
    """Split into (in the sync repos now, gone/renamed) so one missing name can't fail the whole install."""
    r = sh.capture(["pacman", "-Slq"])
    available = set(r.out.split()) if r.ok else set(pkgs)
    return [p for p in pkgs if p in available], [p for p in pkgs if p not in available]
