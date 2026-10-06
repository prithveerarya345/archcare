"""User settings: ~/.config/archcare/config.toml, created with commented defaults on first run."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from archcare.paths import Paths

DEFAULT_CONFIG = """\
# archcare settings. Every key is optional; delete a line to get the default back.

[update]
# order matters: system packages first, then user-level tools
managers = ["pacman", "yay", "snap", "npm", "rustup", "uv", "nvim", "fwupd"]
min_free_percent = 10        # refuse to update with less free space on /
min_battery_percent = 30     # ...or on battery below this
news_url = "https://archlinux.org/feeds/news/"

[clean]
keep_package_versions = 2    # paccache -rk<N>
trash_days = 30              # empty Trash items deleted longer ago than this
cache_days = 60              # remove ~/.cache folders untouched for this long
journal_max = "500M"
downloads_days = 90          # move older Downloads into Downloads/archive/YYYY-MM

[backup]
# restic repository, e.g. "rclone:gdrive:archcare" or "b2:my-bucket:archcare". Empty = backups off.
repository = ""
password_file = "~/.config/archcare/restic-password"
paths = ["~"]
exclude = [
  "~/.cache", "~/.local/share/Trash", "~/Downloads", "~/.cargo/registry", "~/.rustup",
  "~/.npm", "~/.local/share/Steam", "**/node_modules", "**/.venv", "**/__pycache__",
]
keep_daily = 7
keep_weekly = 4
keep_monthly = 6

[alerts]
# phone notifications via ntfy.sh: install the ntfy app, subscribe to a hard-to-guess topic, put it here
ntfy_topic = ""
ntfy_server = "https://ntfy.sh"
disk_warn_percent = 85

[explain]
model = "claude-opus-5-5"
"""


@dataclass
class UpdateCfg:
    managers: list[str] = field(
        default_factory=lambda: ["pacman", "yay", "snap", "npm", "rustup", "uv", "nvim", "fwupd"]
    )
    min_free_percent: int = 10
    min_battery_percent: int = 30
    news_url: str = "https://archlinux.org/feeds/news/"


@dataclass
class CleanCfg:
    keep_package_versions: int = 2
    trash_days: int = 30
    cache_days: int = 60
    journal_max: str = "500M"
    downloads_days: int = 90


@dataclass
class BackupCfg:
    repository: str = ""
    password_file: str = "~/.config/archcare/restic-password"
    paths: list[str] = field(default_factory=lambda: ["~"])
    exclude: list[str] = field(default_factory=list)
    keep_daily: int = 7
    keep_weekly: int = 4
    keep_monthly: int = 6


@dataclass
class AlertsCfg:
    ntfy_topic: str = ""
    ntfy_server: str = "https://ntfy.sh"
    disk_warn_percent: int = 85


@dataclass
class ExplainCfg:
    model: str = "claude-opus-5-5"


@dataclass
class Config:
    update: UpdateCfg = field(default_factory=UpdateCfg)
    clean: CleanCfg = field(default_factory=CleanCfg)
    backup: BackupCfg = field(default_factory=BackupCfg)
    alerts: AlertsCfg = field(default_factory=AlertsCfg)
    explain: ExplainCfg = field(default_factory=ExplainCfg)


def config_path(paths: Paths) -> Path:
    return paths.config_dir / "config.toml"


def load(paths: Paths) -> Config:
    path = config_path(paths)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG)
    raw = tomllib.loads(path.read_text())
    sections = {"update": UpdateCfg, "clean": CleanCfg, "backup": BackupCfg, "alerts": AlertsCfg, "explain": ExplainCfg}
    built = {}
    for name, cls in sections.items():
        known = cls.__dataclass_fields__
        built[name] = cls(**{k: v for k, v in raw.get(name, {}).items() if k in known})
    return Config(**built)
