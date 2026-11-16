"""Settings refuse a bad configuration at boot, one readable line per problem."""

import pytest
from pydantic import ValidationError

from recall_checker.config import Settings, Tier, describe_errors, get_settings

SECRET = "hb_s3cr3t_value"


def settings(**kw) -> Settings:
    return Settings(_env_file=None, **{"homebox_token": SECRET, **kw})


def problems(**kw) -> list[str]:
    with pytest.raises(ValidationError) as exc:
        settings(**kw)
    return describe_errors(exc.value)


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """No settings from the developer's shell or .env; GIT_REVISION etc. untouched."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.chdir(tmp_path)  # no .env picked up
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def test_defaults_are_valid():
    s = settings()
    assert s.source_names() == ["cpsc", "nhtsa"] and s.nhtsa_type_set() == {"C", "E"}


def test_token_required(clean_env):
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)
    assert describe_errors(exc.value) == ["HOMEBOX_TOKEN is required"]


@pytest.mark.parametrize("token", ["", "   "])
def test_token_not_blank(token):
    [line] = problems(homebox_token=token)
    assert line.startswith("HOMEBOX_TOKEN: is required")


def test_token_kept_out_of_repr():
    s = settings(apprise_urls="ntfys://user:pw@ntfy.example.com/x")
    assert SECRET not in repr(s) and "pw@" not in repr(s)


@pytest.mark.parametrize(
    "field", ["homebox_url", "cpsc_base_url", "nhtsa_flat_file_url", "public_url"]
)
@pytest.mark.parametrize("value", ["homebox:7745", "ftp://x.example", "http:///path", "not a url"])
def test_urls_checked_without_echoing(field, value):
    [line] = problems(**{field: value})
    assert line.startswith(f"{field.upper()}: must be an http")
    assert value not in line


def test_optional_urls_may_be_empty():
    s = settings(public_url="", homebox_public_url="")
    assert s.homebox_link_base == "http://homebox:7745"


def test_cron_checked():
    [line] = problems(check_cron="0 7 * *")
    assert line.startswith("CHECK_CRON: not a crontab line") and "expected 5" in line
    assert settings(check_cron=" 0 7 * * * ").check_cron == "0 7 * * *"


@pytest.mark.parametrize("value", ["0", "-1"])
def test_interval_positive(value):
    [line] = problems(check_interval_hours=value)
    assert line.startswith("CHECK_INTERVAL_HOURS:")


def test_sources_checked():
    assert problems(recall_sources="cpsc,fda") == [
        "RECALL_SOURCES: unknown source 'fda' (expected cpsc, nhtsa)"
    ]
    assert problems(recall_sources=" , ")[0].startswith("RECALL_SOURCES: name at least one")
    assert settings(recall_sources=" CPSC ").source_names() == ["cpsc"]


def test_nhtsa_types_checked():
    assert problems(nhtsa_types="C,X") == ["NHTSA_TYPES: unknown type 'X' (expected C, E, T, V)"]
    assert problems(nhtsa_types="") == ["NHTSA_TYPES is empty, but RECALL_SOURCES includes nhtsa"]
    assert settings(nhtsa_types="", recall_sources="cpsc").nhtsa_type_set() == set()


def test_tiers_and_log_level_checked():
    assert problems(min_notify_tier="urgent") == ["MIN_NOTIFY_TIER: expected low, medium or high"]
    assert settings(auto_writeback_tier="HIGH").auto_writeback_tier is Tier.high
    assert problems(log_level="loud")[0].startswith("LOG_LEVEL: expected one of")
    assert settings(log_level="debug").log_level == "DEBUG"


def test_every_problem_reported_at_once():
    lines = problems(homebox_token="", check_cron="bad", recall_sources="x")
    assert [line.split(":")[0] for line in lines] == [
        "HOMEBOX_TOKEN",
        "CHECK_CRON",
        "RECALL_SOURCES",
    ]


def test_boot_exits_with_readable_message(clean_env):
    clean_env.setenv("HOMEBOX_TOKEN", " ")
    clean_env.setenv("HOMEBOX_URL", "http://user:hunter2@homebox")  # valid, not echoed anyway
    clean_env.setenv("CHECK_CRON", "61 * * * *")
    clean_env.setenv("APPRISE_URLS", "ntfys://user:pw@ntfy.example.com/x")
    with pytest.raises(SystemExit) as exc:
        get_settings()
    message = str(exc.value.code)
    assert message.splitlines()[0].startswith("Error: HOMEBOX_TOKEN: is required")
    assert "Error: CHECK_CRON: not a crontab line" in message
    assert "hunter2" not in message and "pw@" not in message
    assert "Traceback" not in message


def test_main_exits_before_serving(clean_env, capsys):
    from recall_checker.__main__ import main

    clean_env.setenv("GIT_REVISION", "abc1234")
    with pytest.raises(SystemExit) as exc:
        main(["serve"])
    assert "Error: HOMEBOX_TOKEN is required" in str(exc.value.code)
    assert "revision abc1234" in capsys.readouterr().err
