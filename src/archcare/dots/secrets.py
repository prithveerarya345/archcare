"""Last line of defence: refuse to commit files that look like they hold credentials.

The manifest's [secret] list keeps known credential folders out entirely; this catches
a token pasted into an otherwise normal config file (e.g. an API key in .bashrc).
Only the *kind* of match is reported, never the secret itself.
"""

import re
from pathlib import Path

PATTERNS: dict[str, re.Pattern[bytes]] = {
    "private key": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "Anthropic key": re.compile(rb"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "OpenAI-style key": re.compile(rb"\bsk-[A-Za-z0-9_\-]{20,}"),
    "GitHub token": re.compile(rb"\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    "AWS access key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    "Slack token": re.compile(rb"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    "Google API key": re.compile(rb"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "assigned secret": re.compile(
        # (?<![a-z0-9]) instead of \b so FOO_API_KEY= and MY_TOKEN= still match
        rb"(?i)(?<![a-z0-9])(api[_-]?key|secret|token|passw(or)?d|auth[_-]?token)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_\-/+=]{16,}"
    ),
}


def scan(path: Path) -> list[str]:
    """Names of secret patterns found in the file (empty list = looks clean)."""
    try:
        data = path.read_bytes()
    except OSError:
        return []
    return [name for name, pat in PATTERNS.items() if pat.search(data)]


def is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return b"\0" in f.read(8192)
    except OSError:
        return False
