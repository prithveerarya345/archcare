"""`archcare update` command."""

import typer
from rich.table import Table

from archcare import sh, state, system
from archcare import update as upd
from archcare.notify import notify
from archcare.ui import config, console, csv, paths


def update(
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Run the checks and show the plan, change nothing."),
    check: bool = typer.Option(False, "--check", help="Only count pending updates (what the daily timer runs)."),
    only: str = typer.Option("", help="Comma-separated managers to run, e.g. pacman,yay"),
    skip: str = typer.Option("", help="Comma-separated managers to leave out."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't stop to ask about Arch News."),
    force: bool = typer.Option(False, "--force", help="Ignore low disk / low battery blockers."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="With --check: print nothing, notify instead."),
) -> None:
    """Update pacman, AUR (yay), snap, npm -g, rustup, uv tools, nvim plugins; check firmware."""
    p, cfg = paths(), config()
    steps = upd.plan(cfg.update, upd.managers(p.home), csv(only), csv(skip))
    pf = upd.preflight(cfg.update, system.disk(), system.battery(), upd.fetch_news(cfg.update.news_url))

    if check:
        _check(steps, pf, quiet)
        return

    # ---- pre-flight
    for note in pf.notes:
        console.print(f"[yellow]note:[/] {note}")
    for b in pf.blockers:
        console.print(f"[red]blocked:[/] {b}")
    if pf.blockers and not force:
        console.print("Fix the above or pass [bold]--force[/].")
        raise typer.Exit(1)
    if pf.news:
        console.print(f"[bold yellow]{len(pf.news)} Arch News post(s) since your last upgrade.[/] Read them first:")
        print_news(pf.news)
        if not dry_run and not yes and not typer.confirm("Read them and ready to continue?", default=False):
            raise typer.Exit(1)

    console.print("\n[bold]Plan[/]")
    for m in steps:
        console.print(f"  {m.name:8} {sh.show(m.update)}" + ("  [dim](report only)[/]" if m.check_only else ""))
    if dry_run:
        return

    # ---- update
    pacnew_before = set(system.pacnew_files())
    results = []
    for m in steps:
        console.rule(f"[bold]{m.name}")
        results.append(upd.run_manager(m))

    # ---- post-flight
    post = upd.postflight(pacnew_before)
    console.rule("[bold]Summary")
    t = Table(box=None, pad_edge=False)
    for col in ("manager", "result", "time", "note"):
        t.add_column(col)
    for r in results:
        colour = {"ok": "green", "failed": "red"}.get(r.status, "dim")
        t.add_row(r.name, f"[{colour}]{r.status}[/]", f"{r.seconds:.0f}s", r.detail)
    console.print(t)
    if post.new_pacnew:
        console.print(f"[yellow]{len(post.new_pacnew)} new .pacnew file(s):[/] run [bold]archcare pacnew[/]")
    if post.failed:
        console.print(f"[red]Failed units:[/] {', '.join(post.failed)}. Try [bold]archcare explain[/]")
    for old, pkgs in post.stale_python.items():
        console.print(f"[yellow]Rebuild for new Python ({old} is left over):[/] yay -S --rebuild {' '.join(pkgs)}")
    if post.reboot:
        console.print("[bold yellow]Kernel was updated: reboot to finish.[/]")

    failed = [r.name for r in results if r.status == "failed"]
    state.set_(p, last_update=state.now(), last_update_failed=failed)
    state.append_history(
        p,
        "updates",
        {
            "results": [r.__dict__ for r in results],
            "reboot": post.reboot,
            "new_pacnew": [str(x) for x in post.new_pacnew],
        },
    )
    if failed:
        raise typer.Exit(1)


def _check(steps: list[upd.Manager], pf: upd.Preflight, quiet: bool) -> None:
    p, cfg = paths(), config()
    pending = {m.name: upd.count_pending(m) for m in steps if m.check}
    total = sum(n for n in pending.values() if n)
    state.set_(p, pending_updates=pending, pending_checked=state.now())
    news = f" {len(pf.news)} Arch News post(s) to read first." if pf.news else ""
    if quiet:
        if total or pf.news:
            notify("archcare: updates waiting", f"{total} updates.{news} Run: archcare update", alerts=cfg.alerts)
        return
    for name, n in pending.items():
        console.print(f"  {name:8} {'?' if n is None else n}")
    console.print(f"\n{total} update(s) waiting.{news}")
    print_news(pf.news)


def print_news(news: list[upd.NewsItem]) -> None:
    for n in news:
        console.print(f"  {n.published:%Y-%m-%d}  [bold]{n.title}[/]\n              {n.link}")
