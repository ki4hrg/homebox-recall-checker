from __future__ import annotations

import ipaddress
from enum import IntEnum
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


class Tier(IntEnum):
    low = 1
    medium = 2
    high = 3

    @classmethod
    def parse(cls, value: str | int | Tier) -> Tier:
        if isinstance(value, Tier):
            return value
        if isinstance(value, int):
            return cls(value)
        return cls[value.strip().lower()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    homebox_url: str = "http://homebox:7745"
    homebox_token: str = ""
    # Browser-facing Homebox URL used for links in notifications/UI (defaults to homebox_url).
    homebox_public_url: str = ""

    # Space/comma separated Apprise URLs, and/or a path to an Apprise config file.
    apprise_urls: str = ""
    apprise_config: str = ""

    check_interval_hours: float = 24.0
    # Optional crontab expression (e.g. "0 7 * * *"); overrides check_interval_hours.
    check_cron: str = ""
    run_on_startup: bool = True

    min_notify_tier: Tier = Tier.medium
    auto_writeback_tier: Tier = Tier.high
    writeback: bool = True
    recall_tag_name: str = "Recall"

    # Items purchased more than this many days after a recall was published are downgraded.
    purchase_grace_days: int = 365

    data_dir: Path = Path("/data")
    ui_host: str = "0.0.0.0"
    ui_port: int = 8080
    # Browser-facing URL of this app, used for links in notifications.
    public_url: str = ""
    # Comma-separated host names the UI answers to (no scheme or port); "*" turns the check off.
    # Unset: PUBLIC_URL's host name, any IP address, and localhost.
    allowed_hosts: str = ""
    log_level: str = "INFO"

    # Comma-separated: cpsc, nhtsa
    recall_sources: str = "cpsc,nhtsa"
    cpsc_base_url: str = "https://www.saferproducts.gov/RestWebServices/Recall"
    nhtsa_flat_file_url: str = "https://static.nhtsa.gov/odi/ffdd/rcl/FLAT_RCL_POST_2010.zip"
    # NHTSA recall types: C child seats, E equipment, T tires, V vehicles
    nhtsa_types: str = "C,E"

    @field_validator("min_notify_tier", "auto_writeback_tier", mode="before")
    @classmethod
    def _tier(cls, v):
        return Tier.parse(v)

    @field_validator("allowed_hosts")
    @classmethod
    def _hosts(cls, v: str) -> str:
        for name in (n.strip() for n in v.split(",")):
            if name in ("", "*") or is_ip(name.strip("[]")):
                continue
            if any(c in name for c in "/:@ "):
                raise ValueError(f"ALLOWED_HOSTS entries are host names only, not {name!r}")
        return v

    def host_rule(self) -> tuple[list[str] | None, bool]:
        """(allowed host names or None for any, whether any IP-literal Host is allowed)."""
        names = [n.strip().strip("[]").lower() for n in self.allowed_hosts.split(",") if n.strip()]
        if "*" in names:
            return None, True
        if names:
            return names, False
        public = (urlsplit(self.public_url).hostname or "") if self.public_url else ""
        return ([public] if public else []), True

    @property
    def db_path(self) -> Path:
        return self.data_dir / "recall-checker.db"

    @property
    def homebox_link_base(self) -> str:
        return (self.homebox_public_url or self.homebox_url).rstrip("/")

    def source_names(self) -> list[str]:
        return [n.strip().lower() for n in self.recall_sources.split(",") if n.strip()]

    def nhtsa_type_set(self) -> set[str]:
        return {t.strip().upper() for t in self.nhtsa_types.split(",") if t.strip()}

    def apprise_url_list(self) -> list[str]:
        return [u for u in self.apprise_urls.replace(",", " ").split() if u]


@lru_cache
def get_settings() -> Settings:
    return Settings()
