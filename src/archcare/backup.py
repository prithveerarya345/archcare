"""Encrypted, deduplicated backups of ~ with restic.

restic does the hard parts (encryption, dedup, snapshots, pruning); archcare decides what to
back up, keeps the password file, runs it on a timer, and proves restores actually work.
"""

import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path

from archcare import sh
from archcare.config import BackupCfg
from archcare.paths import Paths


class BackupError(Exception):
    pass


def env(cfg: BackupCfg, paths: Paths) -> dict[str, str]:
    if not cfg.repository:
        raise BackupError('no repository set: add [backup] repository = "..." to ~/.config/archcare/config.toml')
    return {
        **os.environ,
        "RESTIC_REPOSITORY": cfg.repository,
        "RESTIC_PASSWORD_FILE": str(paths.expand(cfg.password_file)),
    }


def backup_args(cfg: BackupCfg, paths: Paths) -> list[str]:
    args = ["restic", "backup", "--json", "--exclude-caches", "--one-file-system", "--tag", "archcare"]
    for x in cfg.exclude:
        args += ["--exclude", str(paths.expand(x)) if x.startswith("~") else x]
    return args + [str(paths.expand(p)) for p in cfg.paths]


def forget_args(cfg: BackupCfg) -> list[str]:
    return [
        "restic", "forget", "--prune", "--tag", "archcare",
        "--keep-daily", str(cfg.keep_daily),
        "--keep-weekly", str(cfg.keep_weekly),
        "--keep-monthly", str(cfg.keep_monthly),
    ]  # fmt: skip


def ensure_password(cfg: BackupCfg, paths: Paths) -> tuple[Path, bool]:
    """Create a random password file (0600) if missing. Returns (path, created)."""
    pw = paths.expand(cfg.password_file)
    if pw.exists():
        return pw, False
    pw.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(pw, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(secrets.token_urlsafe(32) + "\n")
    return pw, True


def repo_exists(e: dict[str, str]) -> bool:
    return sh.capture(["restic", "cat", "config"], env=e, timeout=60).ok


@dataclass
class Summary:
    files_new: int = 0
    files_changed: int = 0
    data_added: int = 0
    total_bytes: int = 0
    snapshot: str = ""


def parse_summary(json_lines: str) -> Summary:
    """restic --json prints progress lines then one {"message_type": "summary"} object."""
    for line in reversed(json_lines.splitlines()):
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("message_type") == "summary":
            return Summary(
                files_new=msg.get("files_new", 0),
                files_changed=msg.get("files_changed", 0),
                data_added=msg.get("data_added", 0),
                total_bytes=msg.get("total_bytes_processed", 0),
                snapshot=msg.get("snapshot_id", "")[:8],
            )
    return Summary()


def test_restore(e: dict[str, str], probe: Path) -> tuple[bool, str]:
    """Restore one known file from the latest snapshot into a temp dir and check it came back."""
    with tempfile.TemporaryDirectory(prefix="archcare-restore-") as tmp:
        r = sh.capture(["restic", "restore", "latest", "--target", tmp, "--include", str(probe)], env=e, timeout=600)
        restored = Path(tmp) / str(probe).lstrip("/")
        if r.ok and restored.is_file() and restored.stat().st_size > 0:
            return True, f"restored {probe.name} ({restored.stat().st_size} bytes)"
        lines = (r.err or r.out).strip().splitlines()
        return False, lines[-1] if lines else "file missing"
