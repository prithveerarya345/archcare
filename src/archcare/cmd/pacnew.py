"""`archcare pacnew` command."""

import typer
from rich.syntax import Syntax

from archcare import pacnew as pn
from archcare import system
from archcare.ui import console


def pacnew(
    list_: bool = typer.Option(False, "--list", "-l", help="Only list them with change counts."),
    editor: str = typer.Option("nvim", help="Diff editor for merging (run as root)."),
) -> None:
    """Review .pacnew/.pacsave files one by one: keep yours, take the new default, or merge."""
    items = pn.pending(system.pacnew_files())
    if not items:
        console.print("[green]No .pacnew or .pacsave files.[/]")
        return

    if list_:
        for p in items:
            d = pn.diff(p)
            stat = "needs root to read" if d is None else "+{} -{}".format(*pn.diffstat(d))
            tag = "  [red](validated before saving)[/]" if p.validator else ""
            console.print(f"  {p.kind:7} {p.current}  [dim]{stat}[/]{tag}")
        console.print(f"\n{len(items)} file(s). Run [bold]archcare pacnew[/] to go through them.")
        return

    keys = "[bold]k[/]eep yours  [bold]n[/]ew default  [bold]m[/]erge  [bold]s[/]kip  [bold]q[/]uit"
    console.print(f"{len(items)} file(s). {keys}\n")
    for i, p in enumerate(items, 1):
        console.rule(f"[{i}/{len(items)}] {p.current} ({p.kind})")
        if p.kind == "pacsave":
            # .pacsave = your config, saved when its package was removed
            console.print("Saved copy of your config from a package that was removed.")
            if typer.confirm("Delete it?", default=False):
                pn.keep_current(p)
            continue
        d = pn.diff(p)
        if d is None:
            console.print("[yellow]Can't read without root[/]; diff will show in the merge editor.")
        elif not d:
            console.print("[dim]Identical to yours: removing the .pacnew.[/]")
            pn.keep_current(p)
            continue
        else:
            console.print(Syntax("".join(d), "diff", theme="ansi_dark", word_wrap=True))
        if p.validator:
            console.print(f"[red]Will be checked with[/] `{' '.join(p.validator)}` and rolled back if invalid.")

        choice = typer.prompt("  ", default="s", show_default=False).strip().lower()
        if choice == "q":
            break
        ok = True
        if choice == "k":
            ok = pn.keep_current(p)
        elif choice == "n":
            ok = pn.use_new(p)
        elif choice == "m":
            ok = pn.merge(p, editor)
            if ok and typer.confirm("Merged. Delete the .pacnew now?", default=True):
                pn.keep_current(p)
        if not ok:
            console.print("[red]Change rejected or failed; your original file was restored.[/]")
