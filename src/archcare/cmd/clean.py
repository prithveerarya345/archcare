"""`archcare clean` command."""

import typer
from rich.markup import escape
from rich.table import Table

from archcare import clean as cl
from archcare import state, system
from archcare.ui import config, console, csv, human, paths


def clean(
    yes: bool = typer.Option(False, "--yes", "-y", help="Actually clean. Without it, only shows what would happen."),
    only: str = typer.Option(
        "", help="Comma-separated tasks: pacman, yay, orphans, trash, cache, journal, docker, downloads"
    ),
) -> None:
    """Reclaim space: pacman cache, orphans, Trash, ~/.cache, journal, docker, Downloads. Dry run by default."""
    p, cfg = paths(), config()
    tasks = cl.all_tasks(p.home, cfg.clean)
    wanted = csv(only) or list(tasks)
    unknown = set(wanted) - set(tasks)
    if unknown:
        console.print(f"[red]Unknown task(s):[/] {', '.join(sorted(unknown))}")
        raise typer.Exit(2)

    before = system.disk()
    measured: list[cl.Task] = []
    with console.status("Measuring…"):
        for name in wanted:
            measured.append(tasks[name]())

    t = Table(box=None, pad_edge=False)
    t.add_column("task")
    t.add_column("reclaim", justify="right")
    t.add_column("what")
    for task in measured:
        size = "?" if task.reclaim is None else (human(task.reclaim) if task.reclaim else "-")
        t.add_row(escape(task.name), size, escape(task.summary), style="dim" if task.empty else None)
    console.print(t)
    total = sum(task.reclaim or 0 for task in measured)
    console.print(f"\n[bold]~{human(total)}[/] reclaimable.")

    todo = [task for task in measured if not task.empty]
    if not yes:
        if todo:
            console.print("Dry run. Run [bold]archcare clean --yes[/] to clean (you'll be asked for sudo).")
        return

    failed = []
    for task in todo:
        console.rule(task.name)
        if not task.apply():
            failed.append(task.name)
    freed = system.disk().free - before.free
    console.print(
        f"\nFreed [bold]{human(max(freed, 0))}[/]." + (f" [red]Failed:[/] {', '.join(failed)}" if failed else "")
    )
    state.set_(p, last_clean=state.now())
    state.append_history(p, "cleans", {"freed": freed, "failed": failed, "tasks": [x.name for x in todo]})
    if failed:
        raise typer.Exit(1)
