"""`archcare backup ...` and `archcare restore-packages` commands."""

import shlex

import typer

from archcare import backup as bk
from archcare import packages, sh, state
from archcare.notify import notify
from archcare.ui import config, console, human, paths

app = typer.Typer(help="Encrypted restic backups of your home folder.", invoke_without_command=True)


def _need_restic() -> None:
    if not sh.have("restic"):
        console.print("[red]restic isn't installed.[/] sudo pacman -S restic rclone")
        raise typer.Exit(1)


def _env() -> dict[str, str]:
    try:
        return bk.env(config().backup, paths())
    except bk.BackupError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1) from e


@app.callback()
def run(
    ctx: typer.Context,
    quiet: bool = typer.Option(False, "--quiet", "-q", help="For timers: no output, alert on failure."),
) -> None:
    """Back up now (snapshot + prune old snapshots). Subcommands: init, verify, list, env."""
    if ctx.invoked_subcommand:
        return
    p, cfg = paths(), config()
    if not cfg.backup.repository and quiet:
        return  # backups not set up yet: the timer stays silent, `doctor` nags instead
    _need_restic()
    e = _env()

    packages.export(p.data_dir / "packages")  # inside ~, so the lists ride along in the snapshot
    with console.status("Backing up…"):
        r = sh.capture(bk.backup_args(cfg.backup, p), env=e, timeout=None)
    # restic exit 3 = snapshot made but some files were unreadable (e.g. root-owned): still a backup
    if r.code not in (0, 3):
        msg = (r.err or r.out).strip().splitlines()[-1:] or ["unknown error"]
        notify("archcare: backup FAILED", msg[0], urgent=True, alerts=cfg.alerts)
        if not quiet:
            console.print(f"[red]Backup failed:[/] {msg[0]}")
        state.append_history(p, "backups", {"ok": False, "error": msg[0]})
        raise typer.Exit(1)

    s = bk.parse_summary(r.out)
    with console.status("Pruning old snapshots…"):
        pruned = sh.capture(bk.forget_args(cfg.backup), env=e, timeout=None)
    state.set_(p, last_backup=state.now(), last_backup_snapshot=s.snapshot)
    state.append_history(p, "backups", {"ok": True, **s.__dict__, "partial": r.code == 3})
    if not quiet:
        console.print(
            f"[green]Snapshot {s.snapshot}[/]: {s.files_new} new, {s.files_changed} changed files, "
            f"{human(s.data_added)} added (of {human(s.total_bytes)} scanned)."
        )
        if r.code == 3:
            console.print("[yellow]Some files couldn't be read and were skipped.[/]")
        if not pruned.ok:
            console.print(f"[yellow]Pruning failed:[/] {pruned.err.strip()[-200:]}")


@app.command()
def init() -> None:
    """Create the password file and the restic repository."""
    _need_restic()
    p, cfg = paths(), config()
    e = _env()
    pw, created = bk.ensure_password(cfg.backup, p)
    if created:
        console.print(
            f"[bold yellow]Created {pw}.[/] Copy this password into your password manager now: "
            "without it the backup can never be decrypted."
        )
    if bk.repo_exists(e):
        console.print(f"Repository {cfg.backup.repository} already initialised.")
        return
    if sh.interactive(["env", f"RESTIC_REPOSITORY={e['RESTIC_REPOSITORY']}",
                       f"RESTIC_PASSWORD_FILE={e['RESTIC_PASSWORD_FILE']}", "restic", "init"]) != 0:  # fmt: skip
        raise typer.Exit(1)
    console.print("Ready. Run [bold]archcare backup[/] for the first snapshot.")


@app.command()
def verify(quiet: bool = typer.Option(False, "--quiet", "-q")) -> None:
    """Prove the backup works: check 5% of stored data and restore a real file."""
    _need_restic()
    p, cfg = paths(), config()
    e = _env()
    with console.status("Checking repository…"):
        check = sh.capture(["restic", "check", "--read-data-subset=5%"], env=e, timeout=None)
        ok, detail = bk.test_restore(e, p.expand("~/.config/archcare/config.toml"))
    passed = check.ok and ok
    state.set_(p, last_verify=state.now(), last_verify_ok=passed)
    if not passed:
        why = detail if not ok else check.err.strip()[-200:]
        notify("archcare: backup verification FAILED", why, urgent=True, alerts=cfg.alerts)
    if not quiet:
        console.print("[green]✓ repository intact[/]" if check.ok else "[red]✗ repository check failed[/]")
        console.print(("[green]✓ " if ok else "[red]✗ ") + f"test restore: {detail}[/]")
    if not passed:
        raise typer.Exit(1)


@app.command("list")
def list_() -> None:
    """Show snapshots."""
    _need_restic()
    sh.interactive(["env", *[f"{k}={v}" for k, v in _env().items() if k.startswith("RESTIC_")], "restic", "snapshots"])


@app.command("env")
def env_() -> None:
    """Print exports so you can run restic yourself (e.g. `eval $(archcare backup env)`)."""
    for k, v in _env().items():
        if k.startswith("RESTIC_"):
            print(f"export {k}={shlex.quote(v)}")


def restore_packages(
    from_dir: str = typer.Option("", "--from", help="Folder with native.txt / aur.txt (default: the saved lists)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Install. Without it, only shows what would be installed."),
) -> None:
    """Reinstall every package from the saved lists (for a fresh install)."""
    p = paths()
    src = p.expand(from_dir) if from_dir else p.data_dir / "packages"
    native = packages.read_list(src / "native.txt")
    aur = packages.read_list(src / "aur.txt")
    if not native and not aur:
        console.print(f"[red]No package lists in {src}.[/] They're written by `archcare backup` and `dots sync`.")
        raise typer.Exit(1)
    repo_ok, gone = packages.installable_from_repos(native)
    console.print(f"{len(repo_ok)} repo packages, {len(aur)} AUR packages.")
    if gone:
        console.print(f"[yellow]No longer in the repos (skipped):[/] {', '.join(gone)}")
    if not yes:
        console.print("Dry run. Add [bold]--yes[/] to install.")
        return
    if repo_ok and sh.interactive(["sudo", "pacman", "-S", "--needed", *repo_ok]) != 0:
        raise typer.Exit(1)
    if aur and sh.have("yay") and sh.interactive(["yay", "-S", "--needed", *aur]) != 0:
        raise typer.Exit(1)
