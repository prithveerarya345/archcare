"""`archcare update`: one safe command for every package manager on the machine.

Order of operations:
  1. pre-flight   Arch News since your last upgrade, free disk, battery
  2. update       pacman -> yay (AUR) -> snap -> npm -g -> rustup -> uv tools -> nvim plugins
                  (+ fwupd, which only reports firmware updates, never installs them)
  3. post-flight  new .pacnew files, failed units, reboot needed, Python packages to rebuild

On Arch, unattended upgrades are how systems break, so the timer only runs `--check`.
"""

import os
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from archcare import sh, system
from archcare.config import UpdateCfg

# ---------------------------------------------------------------- package managers


@dataclass(frozen=True)
class Manager:
    name: str
    binary: str
    update: list[str]
    check: list[str] | None = None  # prints one pending update per line
    check_only: bool = False  # report, never install (firmware)
    usable: Callable[[], bool] = lambda: True  # extra condition beyond "binary exists"
    skip_lines: tuple[str, ...] = ()  # header / "all up to date" lines in check output

    def available(self) -> bool:
        return sh.have(self.binary) and self.usable()


def _npm_needs_sudo() -> bool:
    r = sh.capture(["npm", "config", "get", "prefix"])
    prefix = Path(r.out.strip() or "/usr")
    return not os.access(prefix / "lib" / "node_modules", os.W_OK)


def _uv_has_tools() -> bool:
    r = sh.capture(["uv", "tool", "list"])
    return r.ok and "No tools installed" not in r.out + r.err


def managers(home: Path) -> dict[str, Manager]:
    npm = ["npm", "update", "-g"]
    if sh.have("npm") and _npm_needs_sudo():
        npm = ["sudo", *npm]
    return {
        m.name: m
        for m in [
            Manager("pacman", "pacman", ["sudo", "pacman", "-Syu"], check=["checkupdates"]),
            Manager("yay", "yay", ["yay", "-Sua"], check=["yay", "-Qua"]),
            Manager(
                "snap",
                "snap",
                ["sudo", "snap", "refresh"],
                check=["snap", "refresh", "--list"],
                skip_lines=("Name ", "All snaps up to date"),
            ),
            Manager("npm", "npm", npm, check=["npm", "outdated", "-g", "--parseable"]),
            Manager("rustup", "rustup", ["rustup", "update"]),
            Manager("uv", "uv", ["uv", "tool", "upgrade", "--all"], usable=_uv_has_tools),
            Manager(
                "nvim",
                "nvim",
                ["nvim", "--headless", "+Lazy! sync", "+qa"],
                usable=(home / ".local/share/nvim/lazy").is_dir,
            ),
            Manager("fwupd", "fwupdmgr", ["fwupdmgr", "get-updates"], check_only=True),
        ]
    }


def plan(cfg: UpdateCfg, all_managers: dict[str, Manager], only: list[str], skip: list[str]) -> list[Manager]:
    """Managers to run, in config order, filtered by --only/--skip and what's installed."""
    names = [n for n in cfg.managers if n in all_managers]
    if only:
        names = [n for n in names if n in only]
    names = [n for n in names if n not in skip]
    return [all_managers[n] for n in names if all_managers[n].available()]


def count_pending(m: Manager) -> int | None:
    if not m.check:
        return None
    r = sh.capture(m.check, timeout=180)
    if m.name == "pacman" and r.code == 2:  # checkupdates: no updates
        return 0
    lines = [ln for ln in r.out.splitlines() if ln.strip() and not ln.startswith(m.skip_lines)]
    return len(lines) if (r.ok or lines) else None


# ---------------------------------------------------------------- pre-flight


@dataclass
class NewsItem:
    title: str
    link: str
    published: datetime


def fetch_news(url: str, timeout: float = 10) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, TimeoutError):
        return None


def parse_news(rss: str, since: datetime | None) -> list[NewsItem]:
    """News posts newer than `since` (all posts if we don't know when you last upgraded)."""
    items = []
    for it in ET.fromstring(rss).iter("item"):
        try:
            published = parsedate_to_datetime(it.findtext("pubDate", ""))
        except (TypeError, ValueError):
            continue
        if since is None or published > since:
            items.append(NewsItem(it.findtext("title", "").strip(), it.findtext("link", "").strip(), published))
    return sorted(items, key=lambda n: n.published)


@dataclass
class Preflight:
    blockers: list[str] = field(default_factory=list)  # stop unless --force
    news: list[NewsItem] = field(default_factory=list)  # needs a human to read first
    notes: list[str] = field(default_factory=list)


def preflight(cfg: UpdateCfg, disk: system.Disk, battery: system.Battery | None, news_rss: str | None) -> Preflight:
    pf = Preflight()
    if disk.free_percent < cfg.min_free_percent:
        pf.blockers.append(
            f"only {disk.free_percent:.0f}% free on / (need {cfg.min_free_percent}%); run `archcare clean` first"
        )
    if battery and not battery.charging and battery.capacity < cfg.min_battery_percent:
        pf.blockers.append(
            f"battery at {battery.capacity}% and not charging (need {cfg.min_battery_percent}%); "
            "a power cut mid-upgrade can leave the system unbootable"
        )
    if news_rss is None:
        pf.notes.append("couldn't fetch Arch News; check https://archlinux.org/news/ yourself")
    else:
        pf.news = parse_news(news_rss, system.last_full_upgrade())
    return pf


# ---------------------------------------------------------------- post-flight


@dataclass
class Postflight:
    new_pacnew: list[Path]
    failed: list[str]
    reboot: bool
    stale_python: dict[str, list[str]]


def postflight(pacnew_before: set[Path]) -> Postflight:
    return Postflight(
        new_pacnew=[p for p in system.pacnew_files() if p not in pacnew_before],
        failed=system.failed_units() + [f"{u} (user)" for u in system.failed_units(user=True)],
        reboot=system.reboot_needed(),
        stale_python=system.stale_python_packages(),
    )


# ---------------------------------------------------------------- run


@dataclass
class StepResult:
    name: str
    status: str  # ok | failed | skipped
    seconds: float = 0.0
    detail: str = ""


def run_manager(m: Manager) -> StepResult:
    start = time.monotonic()
    if m.check_only:
        r = sh.capture(m.update, timeout=120)
        # fwupdmgr get-updates: exit 2 = nothing to do
        detail = "no firmware updates" if r.code == 2 else (r.out.strip().splitlines() or ["see fwupdmgr"])[-1]
        return StepResult(m.name, "ok", time.monotonic() - start, detail)
    code = sh.interactive(m.update)
    status = "ok" if code == 0 else "failed"
    return StepResult(m.name, status, time.monotonic() - start, "" if code == 0 else f"exit code {code}")
