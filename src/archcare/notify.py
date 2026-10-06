"""Tell the user when something needs attention: desktop popup now, phone (ntfy) later."""

import shutil
import subprocess


def notify(title: str, body: str, urgent: bool = False) -> None:
    if shutil.which("notify-send"):
        subprocess.run(
            ["notify-send", "-a", "archcare", "-u", "critical" if urgent else "normal", title, body],
            check=False,
        )
    # TODO: ntfy push to phone (POST https://ntfy.sh/<topic> with the body)
