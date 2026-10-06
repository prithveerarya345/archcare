"""Where archcare keeps its config, data and state.

Everything hangs off one `home` so tests can point it at a temp directory.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    home: Path

    @classmethod
    def default(cls) -> "Paths":
        return cls(Path.home())

    @property
    def config_dir(self) -> Path:
        return self.home / ".config" / "archcare"

    @property
    def manifest(self) -> Path:
        return self.config_dir / "dotfiles.toml"

    @property
    def data_dir(self) -> Path:
        return self.home / ".local" / "share" / "archcare"

    @property
    def dots_repo(self) -> Path:
        return self.data_dir / "dotfiles"

    @property
    def state_dir(self) -> Path:
        return self.home / ".local" / "state" / "archcare"

    def expand(self, p: str) -> Path:
        """'~/.bashrc' -> /home/<user>/.bashrc (relative to self.home)."""
        if p == "~":
            return self.home
        if p.startswith("~/"):
            return self.home / p[2:]
        return Path(p)

    def tilde(self, p: Path) -> str:
        """/home/<user>/.bashrc -> '~/.bashrc'."""
        try:
            return "~/" + p.relative_to(self.home).as_posix()
        except ValueError:
            return p.as_posix()
