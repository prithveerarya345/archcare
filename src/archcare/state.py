"""Small JSON file of facts archcare remembers between runs (last backup, last update...)."""

import json
import time
from pathlib import Path
from typing import Any

from archcare.paths import Paths


def _file(paths: Paths) -> Path:
    return paths.state_dir / "state.json"


def load(paths: Paths) -> dict[str, Any]:
    try:
        return json.loads(_file(paths).read_text())
    except (OSError, ValueError):
        return {}


def set_(paths: Paths, **values: Any) -> None:
    data = load(paths) | values
    f = _file(paths)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(f)  # atomic: a crash never leaves half a file


def now() -> float:
    return time.time()


def append_history(paths: Paths, name: str, record: dict[str, Any]) -> None:
    """One JSON object per line in <state>/<name>.jsonl, for later analysis."""
    f = paths.state_dir / f"{name}.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a") as fh:
        fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **record}) + "\n")
