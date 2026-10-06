"""Helpers shared by every command: one console, lazy config, small parsers."""

from rich.console import Console

from archcare import config as cfgmod
from archcare.paths import Paths

console = Console()


def paths() -> Paths:
    return Paths.default()


def config() -> cfgmod.Config:
    return cfgmod.load(paths())


def csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
