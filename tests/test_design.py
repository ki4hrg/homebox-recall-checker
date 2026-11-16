"""The design rules a test can hold (standards docs/design.md), read from the files themselves:

- every text/background token pair meets WCAG AA (4.5:1) in both themes, and every control's
  edge (border, focus ring) 3:1 against the surfaces it sits on. A new pair a rule uses gets a
  line in TEXT_PAIRS or UI_PAIRS;
- both themes set every standard token, nothing else, and the two dark blocks agree;
- the rules use the tokens, never a raw colour, and never truncate text;
- the theme script in <head> is render-blocking and comes before the stylesheet, so a stored
  theme never flashes.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from recall_checker.web.app import create_app

from .test_runner import CPSC, env  # noqa: F401  (fixture)

WEB = Path(__file__).parent.parent / "src" / "recall_checker" / "web"
CSS = (WEB / "static" / "style.css").read_text()

STANDARD = [
    "page",
    "card",
    "sunk",
    "line",
    "ink",
    "ink-2",
    "ink-3",
    "accent",
    "accent-ink",
    "accent-soft",
    "ok",
    "ok-ink",
    "ok-soft",
    "warn",
    "warn-ink",
    "warn-soft",
    "danger",
    "danger-ink",
    "danger-soft",
]
SURFACES = ["page", "card", "sunk"]
TEXT = ["ink", "ink-2", "ink-3"]

TEXT_PAIRS = [
    # Text, links and status colours as text, on every surface (a secondary button's hover and
    # the disabled button are `sunk`).
    *[(fg, bg) for fg in [*TEXT, "accent", "ok", "warn", "danger"] for bg in SURFACES],
    # The selected filter and theme button; notices; the Dismiss button's hover.
    ("accent", "accent-soft"),
    ("ink", "accent-soft"),
    ("ok", "ok-soft"),
    ("warn", "warn-soft"),
    ("danger", "danger-soft"),
    # Solid fills: the primary button, the high and medium tier badges.
    ("accent-ink", "accent"),
    ("ok-ink", "ok"),
    ("warn-ink", "warn"),
    ("danger-ink", "danger"),
]
# Edges that show where a control is: button and filter borders (ink-3), the focus ring and the
# selected filter (accent), the Dismiss button (danger), on the surfaces they sit on.
UI_PAIRS = [(fg, bg) for fg in ["ink-3", "accent", "danger"] for bg in ["page", "card"]]


def block(opener: str) -> dict[str, str]:
    """The ``--color-*`` values declared directly in the block that opens with ``opener``."""
    start = CSS.index(opener)
    body = CSS[CSS.index("{", start) + 1 : CSS.index("}", start)]
    return {
        name: value.lower()
        for name, value in re.findall(r"--color-([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", body)
    }


LIGHT = block(":root {")
DARK_BY_SYSTEM = block(':root:not([data-theme="light"])')
DARK = block(':root[data-theme="dark"]')
THEMES = [("light", LIGHT), ("dark", DARK)]


def luminance(hex_colour: str) -> float:
    def channel(i: int) -> float:
        c = int(hex_colour[i : i + 2], 16) / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = channel(1), channel(3), channel(5)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted([luminance(a), luminance(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_contrast_maths():
    assert contrast("#000000", "#ffffff") == pytest.approx(21)
    assert contrast("#ffffff", "#7aa2ff") == pytest.approx(2.49, abs=0.01)  # 0.3.0's dark button


@pytest.mark.parametrize("name, theme", THEMES)
def test_every_standard_token_and_nothing_else(name, theme):
    assert [t for t in STANDARD if t not in theme] == []
    # An app colour token is recorded in docs/DECISIONS.md and added to the pairs first.
    assert [t for t in theme if t not in STANDARD] == []


def test_the_two_dark_blocks_agree():
    assert DARK_BY_SYSTEM == DARK


@pytest.mark.parametrize("name, theme", THEMES)
def test_text_pairs_meet_aa(name, theme):
    failing = [
        f"{fg} on {bg}: {contrast(theme[fg], theme[bg]):.2f}"
        for fg, bg in TEXT_PAIRS
        if contrast(theme[fg], theme[bg]) < 4.5
    ]
    assert failing == []


@pytest.mark.parametrize("name, theme", THEMES)
def test_control_edges_meet_aa(name, theme):
    failing = [
        f"{fg} on {bg}: {contrast(theme[fg], theme[bg]):.2f}"
        for fg, bg in UI_PAIRS
        if contrast(theme[fg], theme[bg]) < 3
    ]
    assert failing == []


# Everything after the token blocks: the rules the pages are styled by.
RULES = CSS[CSS.index("}", CSS.index(':root[data-theme="dark"]')) + 1 :]


def test_rules_use_tokens_not_raw_colours():
    without_comments = re.sub(r"/\*.*?\*/", "", RULES, flags=re.S)
    colour = r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(|oklch\(|\b(?:white|black)\b"
    raw = re.findall(colour, without_comments)
    assert raw == []


def test_rules_name_only_tokens_that_exist():
    declared = set(re.findall(r"(--[\w-]+):", CSS))
    used = set(re.findall(r"var\((--[\w-]+)\)", CSS))
    assert used - declared == set()


def test_no_alpha_under_text():
    # No alpha under text: hover and pressed states move to another solid token.
    assert "opacity" not in RULES


def test_no_truncation():
    assert not re.search(r"text-overflow|line-clamp", RULES)


def test_controls_are_44px():
    for selector in ("button {", ".back, .filters a {", "header h1 a {"):
        rule = RULES[RULES.index(selector) :]
        assert "min-height: 44px" in rule[: rule.index("}")], selector


# --- the theme script ---


@pytest.fixture
def client(env):  # noqa: F811
    runner, _, _, router = env
    router.route(host="checker.test").pass_through()
    return TestClient(create_app(runner), base_url="http://checker.test")


def test_theme_script_is_render_blocking_and_before_the_stylesheet(client):
    head = client.get("/").text.split("</head>")[0]
    tag = re.search(r'<script[^>]*src="/static/theme\.js"[^>]*>', head)
    assert tag and not re.search(r"\b(defer|async|type=\"module\")", tag.group(0))
    assert head.index("/static/theme.js") < head.index("/static/style.css")
    assert re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", head) == []


def test_theme_script_is_served_as_javascript(client):
    resp = client.get("/static/theme.js")
    assert resp.status_code == 200
    kind = resp.headers["content-type"].split(";")[0]
    assert kind in ("text/javascript", "application/javascript")
    assert "script-src 'self'" in resp.headers["content-security-policy"]


def test_theme_choice_is_hidden_until_the_script_shows_it(client):
    page = client.get("/").text
    choice = re.search(r"<div[^>]*data-theme-choice[^>]*>", page)
    assert choice and "hidden" in choice.group(0)
    for value in ("light", "dark", "system"):
        assert f'data-theme-value="{value}"' in page
