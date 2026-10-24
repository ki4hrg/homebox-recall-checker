from __future__ import annotations

from typing import Protocol


class Notifier(Protocol):
    @property
    def configured(self) -> bool: ...

    def send(self, title: str, body: str) -> bool:
        """Send a markdown notification; return True on success."""
        ...

    def test(self, link: str = "") -> tuple[bool, str]:
        """Send a test message; return (ok, human-readable detail)."""
        ...
