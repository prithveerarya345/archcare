"""`archcare doctor` command."""

import typer
from rich.markup import escape
from rich.table import Table

from archcare import doctor as dr
from archcare import state
from archcare.notify import notify
from archcare.ui import config, console, paths

ICON = {dr.OK: "[green]✓[/]", dr.INFO: "[blue]i[/]", dr.WARN: "[yellow]![/]", dr.FAIL: "[red]✗[/]"}


def doctor(
    notify_: bool = typer.Option(False, "--notify", help="For timers: no output, alert if anything is warn/fail."),
) -> None:
    """One health report: failed units, journal, disk, SSD, battery, updates, mirrors, backups, timers."""
    p, cfg = paths(), config()
    results = dr.run(p, cfg)
    state.set_(p, last_doctor=state.now(), doctor_worst=dr.worst(results))

    if notify_:
        bad = [r for r in results if r.level in (dr.WARN, dr.FAIL)]
        if bad:
            body = "\n".join(f"{r.name}: {r.detail}" for r in bad[:6])
            notify("archcare: health check", body, urgent=dr.worst(bad) == dr.FAIL, alerts=cfg.alerts)
        return

    t = Table(box=None, pad_edge=False, show_header=False)
    t.add_column(width=1)
    t.add_column(style="bold")
    t.add_column()
    for r in results:
        detail = escape(r.detail) + (f"\n[dim]→ {escape(r.fix)}[/]" if r.fix and r.level != dr.OK else "")
        t.add_row(ICON[r.level], r.name, detail)
    console.print(t)
    if dr.worst(results) == dr.FAIL:
        raise typer.Exit(1)
