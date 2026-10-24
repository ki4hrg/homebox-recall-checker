from __future__ import annotations

import logging
import socket

import apprise

log = logging.getLogger(__name__)


class AppriseNotifier:
    def __init__(self, urls: list[str], config_path: str = ""):
        self.apobj = apprise.Apprise()
        for url in urls:
            if not self.apobj.add(url):
                log.error("Apprise rejected a notification URL (check its syntax): %s",
                          url.split("://")[0] + "://…")
        if config_path:
            cfg = apprise.AppriseConfig()
            if cfg.add(config_path):
                self.apobj.add(cfg)
            else:
                log.error("Could not load Apprise config %s", config_path)

    @property
    def configured(self) -> bool:
        return len(self.apobj) > 0

    def send(self, title: str, body: str) -> bool:
        if not self.configured:
            log.warning("No Apprise URLs configured; notification not sent: %s", title)
            return False
        ok = self.apobj.notify(title=title, body=body, body_format=apprise.NotifyFormat.MARKDOWN)
        if not ok:
            log.error("Apprise failed to deliver to one or more targets")
        return bool(ok)

    def test(self, link: str = "") -> tuple[bool, str]:
        if not self.configured:
            return False, "No notification targets configured (set APPRISE_URLS or APPRISE_CONFIG)."
        body = (f"Test notification from homebox-recall-checker on {socket.gethostname()}. "
                "If you can read this, recall alerts will reach you.")
        if link:
            body += f"\n\n[Open recall checker]({link})"
        # Capture Apprise's own log lines so a failure says *why* (DNS, refused, 401, ...).
        with apprise.LogCapture(level=logging.WARNING) as captured:
            ok = bool(self.apobj.notify(title="Homebox Recall Checker: test",
                                        body=body, body_format=apprise.NotifyFormat.MARKDOWN))
        n = len(self.apobj)
        if ok:
            return True, f"Sent to {n} target{'s' if n != 1 else ''}."
        lines = [ln.split(" - ", 2)[-1] for ln in captured.getvalue().splitlines() if ln.strip()]
        return False, "Delivery failed. " + (" ".join(lines[-3:]) or "See the container log.")
