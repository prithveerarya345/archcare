"""`archcare run daily|weekly|monthly`: what the systemd timers execute.

Each job is independent: one failing (say, offline during backup) never stops the others.
Everything is quiet; problems surface as notifications.
"""

from collections.abc import Callable

import typer

from archcare import clean as cl
from archcare.notify import notify
from archcare.ui import config, console, human, paths

RECLAIM_ALERT = 5 * 1024**3  # weekly: nudge when this much space could be freed


def _jobs(schedule: str) -> list[tuple[str, Callable[[], None]]]:
    # imported lazily: cli imports this module
    from archcare.cli import dots_sync
    from archcare.cmd.backup import run as backup_run
    from archcare.cmd.backup import verify
    from archcare.cmd.doctor import doctor
    from archcare.cmd.update import update

    if schedule == "daily":
        return [
            ("dotfiles", lambda: dots_sync(dry_run=False, push=None, quiet=True)),
            (
                "update check",
                lambda: update(dry_run=False, check=True, only="", skip="", yes=False, force=False, quiet=True),
            ),
            ("backup", lambda: backup_run(_NoSub(), quiet=True)),
            ("doctor", lambda: doctor(notify_=True)),
        ]
    if schedule == "weekly":
        return [("cleanup report", _weekly_clean_report)]
    if schedule == "monthly":
        return [("backup verify", lambda: verify(quiet=True) if config().backup.repository else None)]
    raise typer.BadParameter("schedule must be daily, weekly or monthly")


class _NoSub:
    invoked_subcommand = None


def _weekly_clean_report() -> None:
    cfg = config()
    tasks = cl.all_tasks(paths().home, cfg.clean)
    total = sum(t.reclaim or 0 for t in (tasks[n]() for n in ("pacman", "yay", "trash", "cache", "journal")))
    if total >= RECLAIM_ALERT:
        notify("archcare: space to reclaim", f"~{human(total)} can be freed. Run: archcare clean", alerts=cfg.alerts)


def run(schedule: str = typer.Argument(..., help="daily | weekly | monthly")) -> None:
    """Run a scheduled job set (used by the systemd timers)."""
    failed = []
    for name, job in _jobs(schedule):
        try:
            job()
        except typer.Exit as e:
            if e.exit_code:
                failed.append(name)
        except Exception as e:  # keep going: the next job may still succeed
            failed.append(f"{name} ({e})")
    if failed:
        console.print(f"failed: {', '.join(failed)}")
        notify(f"archcare {schedule}: {len(failed)} job(s) failed", ", ".join(failed), alerts=config().alerts)
        raise typer.Exit(1)
