"""archcare command line."""

import subprocess
from importlib import resources
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from archcare.dots import manifest as mf
from archcare.dots import store
from archcare.dots.scan import Status, scan
from archcare.notify import notify
from archcare.paths import Paths

app = typer.Typer(help="Keep this Arch machine updated, backed up, clean and healthy.", no_args_is_help=True)
dots = typer.Typer(help="Track and back up dotfiles.", no_args_is_help=True)
app.add_typer(dots, name="dots")
console = Console()

STATUS_STYLE = {
    Status.TRACKED: "green",
    Status.PARTIAL: "green",
    Status.UNTRACKED: "yellow",
    Status.IGNORED: "dim",
    Status.SECRET: "red",
    Status.MISSING: "magenta",
}


def _paths() -> Paths:
    return Paths.default()


def _manifest(paths: Paths) -> mf.Manifest:
    if mf.init(paths.manifest):
        console.print(f"Created manifest at [bold]{paths.manifest}[/] with sensible defaults.")
    return mf.load(paths.manifest)


def _tilde(paths: Paths, p: str) -> str:
    return paths.tilde(Path(p).expanduser().absolute())


# ---------------------------------------------------------------- dots


@dots.command("status")
def dots_status(
    all_: bool = typer.Option(False, "--all", "-a", help="Also list tracked/ignored/secret entries."),
) -> None:
    """What dotfiles exist, and which ones still need a decision."""
    paths = _paths()
    entries = scan(paths, _manifest(paths))

    counts = {s: sum(e.status is s for e in entries) for s in Status}
    console.print("  ".join(f"[{STATUS_STYLE[s]}]{s}: {n}[/]" for s, n in counts.items() if n))

    shown = entries if all_ else [e for e in entries if e.status in (Status.UNTRACKED, Status.MISSING)]
    if not shown:
        console.print("[green]Every dotfile is accounted for.[/]")
        return
    t = Table(box=None, pad_edge=False)
    t.add_column("status")
    t.add_column("path", overflow="fold")
    t.add_column("group")
    t.add_column("size", justify="right", no_wrap=True)
    t.add_column("note", style="dim")
    for e in sorted(shown, key=lambda e: (e.status, e.path)):
        t.add_row(
            f"[{STATUS_STYLE[e.status]}]{e.status}[/]",
            e.path,
            e.group or "",
            store.human(e.size) if e.size else "",
            e.hint,
        )
    console.print(t)
    if counts[Status.UNTRACKED]:
        console.print("\nDecide on untracked entries with [bold]archcare dots review[/].")


@dots.command("list")
def dots_list(group: str = typer.Argument(None, help="Only this group.")) -> None:
    """Tracked groups and their paths."""
    paths = _paths()
    m = _manifest(paths)
    for g in m.groups.values():
        if group and g.name != group:
            continue
        console.print(f"[bold]{g.name}[/]")
        for p in g.paths:
            mark = "" if paths.expand(p).exists() else "  [magenta](missing)[/]"
            console.print(f"  {p}{mark}")


@dots.command("track")
def dots_track(path: str, group: str = typer.Option("misc", "--group", "-g")) -> None:
    """Start backing up a file or folder."""
    paths = _paths()
    _manifest(paths)
    mf.add(paths.manifest, "groups", _tilde(paths, path), group)
    console.print(f"Tracking {_tilde(paths, path)} in [bold]{group}[/].")


@dots.command("ignore")
def dots_ignore(pattern: str) -> None:
    """Never back this up (accepts globs like '~/.config/kgame*')."""
    paths = _paths()
    _manifest(paths)
    entry = pattern if pattern.startswith("~") else _tilde(paths, pattern)
    mf.add(paths.manifest, "ignore", entry)
    console.print(f"Ignoring {entry}.")


@dots.command("secret")
def dots_secret(pattern: str) -> None:
    """Mark something as credentials: never copied, never committed."""
    paths = _paths()
    _manifest(paths)
    entry = pattern if pattern.startswith("~") else _tilde(paths, pattern)
    mf.add(paths.manifest, "secret", entry)
    console.print(f"[red]Secret[/]: {entry} will never be backed up.")


@dots.command("review")
def dots_review() -> None:
    """Walk through untracked dotfiles one by one and decide."""
    paths = _paths()
    m = _manifest(paths)
    todo = [e for e in scan(paths, m) if e.status is Status.UNTRACKED]
    if not todo:
        console.print("[green]Nothing to review.[/]")
        return
    groups = list(m.groups)
    console.print(
        f"{len(todo)} untracked. Keys: [bold]t[/]rack  [bold]i[/]gnore  [bold]s[/]ecret  [bold]n[/]ext  [bold]q[/]uit\n"
    )
    for i, e in enumerate(todo, 1):
        note = f"  [dim]{e.hint}[/]" if e.hint else ""
        console.print(f"[{i}/{len(todo)}] [yellow]{e.path}[/]  {store.human(e.size)}{note}")
        choice = typer.prompt("  ", default="n", show_default=False).strip().lower()
        if choice == "q":
            break
        if choice == "t":
            group = typer.prompt(f"  group ({', '.join(groups)} or new)", default="misc")
            mf.add(paths.manifest, "groups", e.path, group)
            groups += [group] if group not in groups else []
        elif choice == "i":
            mf.add(paths.manifest, "ignore", e.path)
        elif choice == "s":
            mf.add(paths.manifest, "secret", e.path)


@dots.command("sync")
def dots_sync(
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would change, touch nothing."),
    push: bool = typer.Option(None, "--push/--no-push", help="Override the manifest's push setting."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="For timers: print less, notify on problems."),
) -> None:
    """Copy tracked dotfiles into the git repo and commit what changed."""
    paths = _paths()
    m = _manifest(paths)
    try:
        res = store.sync(paths, m, dry_run=dry_run, push=push)
    except subprocess.CalledProcessError as err:
        notify("archcare: dotfiles sync failed", (err.stderr or str(err))[:200], urgent=True)
        raise typer.Exit(1) from err

    secret_skips = [r for r, why in res.skipped if why.startswith("looks like")]
    if quiet:
        if secret_skips:
            notify(
                "archcare: possible secret in dotfiles",
                f"{len(secret_skips)} file(s) skipped. Run: archcare dots sync -n",
            )
        return

    verb = "Would copy" if dry_run else "Copied"
    for r in res.changed:
        console.print(f"  [green]+[/] {r}")
    for r in res.removed:
        console.print(f"  [red]-[/] {r}")
    for r, why in res.skipped:
        console.print(f"  [yellow]skip[/] {r} [dim]({why})[/]")
    console.print(f"\n{verb} {len(res.changed)}, removed {len(res.removed)}, skipped {len(res.skipped)}.")
    if res.commit:
        console.print(f"Committed to {paths.dots_repo}" + (" and pushed." if res.pushed else "."))
    elif not dry_run:
        console.print("No changes since last sync.")


@dots.command("restore")
def dots_restore(
    group: str = typer.Argument(None, help="Only restore this group."),
    dry_run: bool = typer.Option(True, "--dry-run/--yes", help="Default only shows what would change."),
) -> None:
    """Put backed-up dotfiles back (e.g. on a fresh install). Overwritten files are saved first."""
    paths = _paths()
    actions = store.restore(paths, _manifest(paths), group, dry_run)
    if not actions:
        console.print("Nothing to restore: your files already match the backup.")
        return
    for p, action in actions:
        console.print(f"  {action:9} {p}")
    if dry_run:
        console.print(f"\n{len(actions)} file(s) would change. Run again with [bold]--yes[/] to restore.")
    else:
        console.print(f"\nRestored {len(actions)} file(s). Old versions are in {paths.state_dir / 'restore-backups'}.")


@dots.command("log")
def dots_log(path: str = typer.Argument(None, help="History of one file, e.g. ~/.bashrc")) -> None:
    """Show the history of your dotfiles (or one file)."""
    paths = _paths()
    args = ["log", "--stat", "--format=%n%C(yellow)%h%Creset %ad  %s", "--date=format:%Y-%m-%d %H:%M"]
    if path:
        args += ["--", "home/" + _tilde(paths, path).removeprefix("~/")]
    subprocess.run(["git", "-C", str(paths.dots_repo), *args], check=False)


# ---------------------------------------------------------------- timers


@app.command("install-timers")
def install_timers(dry_run: bool = typer.Option(False, "--dry-run", "-n")) -> None:
    """Install and enable archcare's systemd user timers."""
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    units = [u for u in resources.files("archcare.systemd").iterdir() if u.name.endswith((".service", ".timer"))]
    for u in units:
        console.print(f"  {unit_dir / u.name}")
        if not dry_run:
            unit_dir.mkdir(parents=True, exist_ok=True)
            (unit_dir / u.name).write_text(u.read_text())
    if dry_run:
        return
    timers = [u.name for u in units if u.name.endswith(".timer")]
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", *timers], check=True)
    console.print(f"Enabled: {', '.join(timers)}. Check with [bold]systemctl --user list-timers[/].")


# ---------------------------------------------------------------- not built yet


def _todo(name: str) -> None:
    console.print(f"[yellow]`archcare {name}` is not built yet.[/] See README.md for the plan.")
    raise typer.Exit(2)


@app.command()
def update() -> None:
    """Update pacman, AUR (yay), snap, npm -g, rustup, uv tools, nvim plugins; check firmware."""
    _todo("update")


@app.command()
def clean() -> None:
    """Reclaim space: pacman cache, orphans, Trash, ~/.cache, journal, docker, Downloads."""
    _todo("clean")


@app.command()
def backup() -> None:
    """Encrypted restic backup of ~ and /etc to cloud storage."""
    _todo("backup")


@app.command()
def pacnew() -> None:
    """Review and merge .pacnew config files safely."""
    _todo("pacnew")


@app.command()
def doctor() -> None:
    """One health report: failed units, disk, SSD, battery, mirrors, backups, timers."""
    _todo("doctor")


@app.command()
def explain() -> None:
    """Ask an LLM to explain recent errors from the journal in plain English."""
    _todo("explain")


def main() -> None:
    app()
