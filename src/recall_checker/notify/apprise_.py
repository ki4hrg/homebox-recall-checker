from __future__ import annotations

import logging

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
