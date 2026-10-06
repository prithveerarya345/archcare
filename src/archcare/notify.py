"""Tell the user when something needs attention: desktop popup, plus phone push via ntfy."""

import urllib.error
import urllib.request

from archcare import sh
from archcare.config import AlertsCfg


def notify(title: str, body: str, urgent: bool = False, alerts: AlertsCfg | None = None) -> None:
    if sh.have("notify-send"):
        sh.capture(["notify-send", "-a", "archcare", "-u", "critical" if urgent else "normal", title, body], timeout=10)
    if alerts and alerts.ntfy_topic:
        push(alerts, title, body, urgent)


def push(alerts: AlertsCfg, title: str, body: str, urgent: bool = False) -> bool:
    """POST to ntfy. Returns False instead of raising: an alert must never crash a job."""
    req = ntfy_request(alerts, title, body, urgent)
    try:
        with urllib.request.urlopen(req, timeout=10):
            return True
    except (urllib.error.URLError, OSError):
        return False


def ntfy_request(alerts: AlertsCfg, title: str, body: str, urgent: bool) -> urllib.request.Request:
    return urllib.request.Request(
        f"{alerts.ntfy_server.rstrip('/')}/{alerts.ntfy_topic}",
        data=body.encode(),
        headers={
            "Title": title.encode("ascii", "replace").decode(),  # HTTP headers must be ASCII
            "Priority": "high" if urgent else "default",
            "Tags": "warning" if urgent else "computer",
        },
        method="POST",
    )
