"""Thin wrappers around subprocess so every feature runs commands the same way.

Tests monkeypatch `capture` / `interactive` instead of touching the real system.
"""

import shlex
import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class Result:
    code: int
    out: str
    err: str = ""

    @property
    def ok(self) -> bool:
        return self.code == 0


def capture(cmd: list[str], timeout: float | None = 120, env: dict[str, str] | None = None) -> Result:
    """Run quietly and return output. Never raises: a missing binary is exit code 127."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, check=False)
    except FileNotFoundError:
        return Result(127, "", f"{cmd[0]}: not found")
    except subprocess.TimeoutExpired:
        return Result(124, "", f"{cmd[0]}: timed out after {timeout}s")
    return Result(p.returncode, p.stdout, p.stderr)


def interactive(cmd: list[str]) -> int:
    """Run attached to the terminal (pacman prompts, sudo password, nvim)."""
    try:
        return subprocess.run(cmd, check=False).returncode
    except FileNotFoundError:
        return 127


def have(binary: str) -> bool:
    return shutil.which(binary) is not None


def show(cmd: list[str]) -> str:
    return shlex.join(cmd)
