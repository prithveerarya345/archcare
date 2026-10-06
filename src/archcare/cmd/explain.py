"""`archcare explain` command."""

import typer
from rich.markdown import Markdown
from rich.panel import Panel

from archcare import explain as ex
from archcare import state
from archcare.ui import config, console, paths


def explain(
    boot: str = typer.Option("0", help="Which boot: 0 = current, -1 = previous (useful after a crash)."),
    show: bool = typer.Option(False, "--show", help="Print exactly what would be sent, then stop."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Send without asking."),
) -> None:
    """Explain this boot's errors in plain English, with suggested fixes (never runs anything)."""
    cfg = config()
    with console.status("Collecting logs…"):
        context = ex.redact(ex.gather(boot))

    lines = context.count("\n") + 1
    if show:
        print(context)  # plain print: no wrapping, exactly what would be sent
        return
    console.print(
        f"Collected {lines} lines (failed units, journal errors, last upgrade), with usernames, "
        f"host, IPs, emails and keys redacted. See them with [bold]--show[/]."
    )
    if not yes and not typer.confirm(f"Send to Claude ({cfg.explain.model})?", default=True):
        raise typer.Exit(1)

    try:
        with console.status("Asking Claude…"):
            answer = ex.ask(context, cfg.explain.model)
    except ex.ExplainError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1) from e
    console.print(Panel(Markdown(answer), title="diagnosis", border_style="cyan"))
    state.append_history(paths(), "explains", {"boot": boot, "lines_sent": lines})
