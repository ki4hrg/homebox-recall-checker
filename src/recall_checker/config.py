from __future__ import annotations

import ipaddress
import os
import sys
from enum import IntEnum
from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from urllib.parse import urlsplit

from apscheduler.triggers.cron import CronTrigger
from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SOURCES = ("cpsc", "nhtsa")
NHTSA_TYPES = ("C", "E", "T", "V")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


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
    # Required: an API key. repr=False keeps it out of reprs and tracebacks.
    homebox_token: str = Field(repr=False)
    # Browser-facing Homebox URL used for links in notifications/UI (defaults to homebox_url).
    homebox_public_url: str = ""

    # Space/comma separated Apprise URLs, and/or a path to an Apprise config file.
    # Carry the notification services' own credentials.
    apprise_urls: str = Field("", repr=False)
    apprise_config: str = ""

    check_interval_hours: float = Field(24.0, gt=0)
    # Optional crontab expression (e.g. "0 7 * * *"); overrides check_interval_hours.
    check_cron: str = ""
    run_on_startup: bool = True

    min_notify_tier: Tier = Tier.medium
    auto_writeback_tier: Tier = Tier.high
    writeback: bool = True
    recall_tag_name: str = "Recall"

    # Items purchased more than this many days after a recall was published are downgraded.
    purchase_grace_days: int = Field(365, ge=0)

    data_dir: Path = Path("/data")
    ui_host: str = "0.0.0.0"
    ui_port: int = Field(8080, ge=1, le=65535)
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
        try:
            return Tier.parse(v)
        except (KeyError, ValueError):
            raise ValueError("expected low, medium or high") from None

    @field_validator("homebox_token")
    @classmethod
    def _token(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("is required (create an API key in Homebox: Profile → API Keys)")
        return v

    @field_validator("homebox_url", "cpsc_base_url", "nhtsa_flat_file_url")
    @classmethod
    def _url(cls, v: str) -> str:
        return check_url(v)

    @field_validator("homebox_public_url", "public_url")
    @classmethod
    def _optional_url(cls, v: str) -> str:
        return check_url(v) if v.strip() else ""

    @field_validator("check_cron")
    @classmethod
    def _cron(cls, v: str) -> str:
        v = v.strip()
        if v:
            try:
                CronTrigger.from_crontab(v)
            except ValueError as exc:
                raise ValueError(f"not a crontab line ({exc})") from None
        return v

    @field_validator("recall_sources")
    @classmethod
    def _sources(cls, v: str) -> str:
        names = [n.strip().lower() for n in v.split(",") if n.strip()]
        if not names:
            raise ValueError(f"name at least one of: {', '.join(SOURCES)}")
        unknown = [n for n in names if n not in SOURCES]
        if unknown:
            raise ValueError(
                f"unknown source {', '.join(map(repr, unknown))} (expected {', '.join(SOURCES)})"
            )
        return ",".join(names)

    @field_validator("nhtsa_types")
    @classmethod
    def _nhtsa_types(cls, v: str) -> str:
        types = [t.strip().upper() for t in v.split(",") if t.strip()]
        unknown = [t for t in types if t not in NHTSA_TYPES]
        if unknown:
            raise ValueError(
                f"unknown type {', '.join(map(repr, unknown))} (expected {', '.join(NHTSA_TYPES)})"
            )
        return ",".join(types)

    @field_validator("log_level")
    @classmethod
    def _log_level(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in LOG_LEVELS:
            raise ValueError(f"expected one of {', '.join(LOG_LEVELS)}")
        return v

    @model_validator(mode="after")
    def _nhtsa_needs_types(self) -> Settings:
        if "nhtsa" in self.source_names() and not self.nhtsa_type_set():
            raise ValueError("NHTSA_TYPES is empty, but RECALL_SOURCES includes nhtsa")
        return self

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


def check_url(value: str) -> str:
    """An http(s) URL with a host. The value isn't echoed: a URL can carry credentials."""
    value = value.strip()
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("must be an http:// or https:// URL with a host name")
    return value


def describe_errors(error: ValidationError) -> list[str]:
    """One line per problem, named by its environment variable. Never includes a value."""
    lines = []
    for e in error.errors(include_input=False, include_url=False):
        name = str(e["loc"][0]).upper() if e["loc"] else ""
        if e["type"] == "missing":
            lines.append(f"{name} is required")
            continue
        message = e["msg"].removeprefix("Value error, ")
        lines.append(f"{name}: {message}" if name else message)
    return lines


@lru_cache
def get_settings() -> Settings:
    """Settings from the environment, or exit 1 with one line per problem."""
    try:
        return Settings()
    except ValidationError as exc:
        problems = "\n".join(f"Error: {line}" for line in describe_errors(exc))
        sys.exit(f"{problems}\nThe configuration is invalid; see the README's Configuration.")


def git_revision() -> str | None:
    """The commit the image was built from (Dockerfile ARG/ENV GIT_REVISION), or None."""
    return os.environ.get("GIT_REVISION", "").strip() or None


def app_version() -> str | None:
    try:
        return package_version("homebox-recall-checker")
    except PackageNotFoundError:
        return None
