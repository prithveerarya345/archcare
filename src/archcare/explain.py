"""`archcare explain`: turn this boot's errors into a plain-English diagnosis.

Collects failed units, de-duplicated journal errors and the last pacman transaction,
redacts identifying details, and asks Claude what's actually wrong and how to fix it.
It only ever *suggests* commands; nothing is executed.
"""

import os
import re
import socket
from collections import Counter
from pathlib import Path

from archcare import sh, system
from archcare.dots.secrets import PATTERNS

SYSTEM_PROMPT = """\
You are an experienced Arch Linux administrator helping the owner of a personal laptop.
You'll get failed systemd units, de-duplicated journal errors from one boot, and the most
recent pacman transaction. Identifying details are redacted as <user>, <host>, <ip>, etc.

Separate real problems from harmless noise (many drivers and desktop services log errors
that don't matter). For each real problem give:
- **What's wrong**, in one plain sentence
- **Likely cause**, tied to the specific log lines
- **Fix**: exact commands, safest first; say when a reboot or a backup is wise

Then list the noise briefly with a short reason each is safe to ignore.
Be concise. If the logs don't show enough to be sure, say what to check next instead of guessing.
"""

_TS_PREFIX = re.compile(r"^\S+\s+")  # short-iso timestamp
_PID = re.compile(r"\[\d+\]")
_HEX = re.compile(r"\b0x[0-9a-f]+\b", re.I)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
# user@1000.service looks like an email but is a systemd unit name, and the key clue
_UNIT_SUFFIXES = (".service", ".socket", ".timer", ".target", ".mount", ".slice", ".scope", ".path", ".device")


def dedupe(lines: list[str], limit: int = 60) -> list[str]:
    """Collapse repeats: same message (ignoring time, pid, addresses) -> one line with a count."""
    counts: Counter[str] = Counter()
    for line in lines:
        msg = _HEX.sub("0x…", _PID.sub("", _TS_PREFIX.sub("", line, count=1))).strip()
        counts[msg] += 1
    return [f"{msg}  (x{n})" if n > 1 else msg for msg, n in counts.most_common(limit)]


def last_transaction(log: Path = system.PACMAN_LOG, limit: int = 40) -> list[str]:
    """Warnings/errors plus a count of upgrades from the most recent pacman -Syu."""
    try:
        lines = log.read_text(errors="replace").splitlines()
    except OSError:
        return []
    start = max((i for i, ln in enumerate(lines) if "starting full system upgrade" in ln), default=None)
    if start is None:
        return []
    tx = lines[start:]
    upgraded = [ln for ln in tx if "[ALPM] upgraded " in ln]
    notable = [ln for ln in tx if re.search(r"warning|error|failed", ln, re.I)]
    kernel = [ln for ln in upgraded if re.search(r"upgraded linux(-lts|-zen)? ", ln)]
    out = [tx[0], f"... {len(upgraded)} packages upgraded"] + kernel + notable[:limit]
    return out


def unit_status(unit: str, user: bool = False) -> str:
    cmd = ["systemctl", "status", "--no-pager", "-n", "15", unit.removesuffix(" (user)")]
    if user:
        cmd.insert(1, "--user")
    r = sh.capture(cmd)
    return (r.out or r.err).strip()


def gather(boot: str = "0") -> str:
    parts = []
    failed_sys, failed_user = system.failed_units(), system.failed_units(user=True)
    if failed_sys or failed_user:
        parts.append("## Failed units")
        parts += [unit_status(u) for u in failed_sys]
        parts += [unit_status(u, user=True) for u in failed_user]
    errors = dedupe(system.journal_errors(boot))
    parts.append(f"## Journal errors this boot ({len(errors)} distinct)")
    parts += errors or ["(none)"]
    tx = last_transaction()
    if tx:
        parts.append("## Last pacman upgrade")
        parts += tx
    parts.append(f"## Kernel\nrunning {os.uname().release}; reboot needed: {system.reboot_needed()}")
    return "\n".join(parts)


def redact(text: str, user: str | None = None, host: str | None = None) -> str:
    user = user or os.environ.get("USER") or Path.home().name
    host = host or socket.gethostname()
    for name, pat in PATTERNS.items():
        text = pat.sub(f"<redacted {name}>".encode(), text.encode()).decode()
    text = _EMAIL.sub(lambda m: m.group(0) if m.group(0).endswith(_UNIT_SUFFIXES) else "<email>", text)
    text = re.sub(r"\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b", "<mac>", text, flags=re.I)
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<ip>", text)
    text = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "<uuid>", text, flags=re.I)
    if host:
        text = re.sub(rf"\b{re.escape(host)}\b", "<host>", text)
    if user and len(user) > 2:  # don't blank out every "ar" in the logs
        text = re.sub(rf"\b{re.escape(user)}\b", "<user>", text)
    return text


class ExplainError(Exception):
    pass


def ask(context: str, model: str) -> str:
    import anthropic  # imported here so the rest of archcare works without network libs loaded

    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            output_config={"effort": "medium"},
            # if a safety classifier declines (logs can look like attack traffic), retry on the recommended model
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": context}],
        )
    except anthropic.AuthenticationError as e:
        raise ExplainError("API key rejected. Check ANTHROPIC_API_KEY (or run `ant auth login`).") from e
    except anthropic.CredentialsError as e:
        raise ExplainError("No Anthropic credentials: export ANTHROPIC_API_KEY or run `ant auth login`.") from e
    except anthropic.RateLimitError as e:
        raise ExplainError("Rate limited by the API; try again in a minute.") from e
    except anthropic.APIStatusError as e:
        raise ExplainError(f"API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise ExplainError("Couldn't reach the Anthropic API (offline?).") from e

    if response.stop_reason == "refusal":
        raise ExplainError("The model declined to analyse these logs.")
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if response.stop_reason == "max_tokens":
        text += "\n\n_(answer was cut off)_"
    return text
