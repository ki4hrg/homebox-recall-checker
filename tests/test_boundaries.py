"""Refusals at the UI's edges: bad input, error pages, notices, links from the feeds, caps."""

import io
import logging
import re
import zipfile
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from recall_checker import messages
from recall_checker.config import Tier
from recall_checker.sources import cpsc, nhtsa
from recall_checker.sources.base import FeedTooLarge, http_url
from recall_checker.web import guards
from recall_checker.web.app import create_app

from .test_runner import CPSC, env  # noqa: F401  (fixture)

BASE = "http://checker.test"
SAMPLE = Path(__file__).parent / "fixtures" / "nhtsa_sample.txt"


@pytest.fixture
def ui(env):  # noqa: F811
    runner, hb, notifier, router = env
    router.route(host="checker.test").pass_through()
    runner.run()
    sleeper = next(r for r in runner.db.list_matches() if r["item_id"] == "i2")
    client = TestClient(create_app(runner), base_url=BASE, raise_server_exceptions=False)
    return runner, client, sleeper["id"], router


def assert_refused(resp, status, *, message):
    assert resp.status_code == status
    assert resp.headers["content-type"].startswith("text/html")
    assert message in resp.text
    assert "Traceback" not in resp.text and '"detail"' not in resp.text


# --- bad input is a 400 with a short message, never a 500 ---


@pytest.mark.parametrize(
    "query, message",
    [
        ("tier=bogus", "Unknown tier. Use one of: low, medium, high."),
        ("tier=HIGH", "Unknown tier."),
        ("status=bogus", "Unknown status. Use one of: new, confirmed, dismissed, all."),
        ("notice=Your+Homebox+was+hacked", "Unknown notice."),
        ("notice=%3Cscript%3E", "Unknown notice."),
    ],
)
def test_index_refuses_unknown_values(ui, query, message):
    _, client, *_ = ui
    resp = client.get(f"/?{query}")
    assert_refused(resp, 400, message=message)
    assert "hacked" not in resp.text and "<script>" not in resp.text


@pytest.mark.parametrize("query", ["", "status=all", "status=dismissed", "tier=low", "tier=high"])
def test_index_accepts_its_own_values(ui, query):
    _, client, *_ = ui
    assert client.get(f"/?{query}").status_code == 200


@pytest.mark.parametrize("path", ["/match/abc", "/match/0", "/match/-1", f"/match/{2**63}"])
def test_match_id_must_be_a_positive_64_bit_int(ui, path):
    _, client, *_ = ui
    assert_refused(client.get(path), 400, message="match_id is missing or not valid")


def test_unknown_match_is_404(ui):
    _, client, *_ = ui
    assert_refused(client.get("/match/999999"), 404, message="No such match.")
    resp = client.post("/match/999999/status", data={"status": "dismissed"})
    assert_refused(resp, 404, message="No such match.")


@pytest.mark.parametrize(
    "data, message",
    [
        ({"status": "deleted"}, "Unknown status."),
        ({"status": ""}, "status is missing or not valid"),
        ({}, "status is missing or not valid"),
    ],
)
def test_status_change_refuses_bad_values(ui, data, message):
    runner, client, match_id, _ = ui
    before = runner.db.get_match(match_id)["status"]
    resp = client.post(f"/match/{match_id}/status", data=data, follow_redirects=False)
    assert_refused(resp, 400, message=message)
    assert runner.db.get_match(match_id)["status"] == before


def test_routing_errors_use_the_same_page(ui):
    _, client, *_ = ui
    assert_refused(client.get("/nowhere"), 404, message="Not found.")
    resp = client.get("/run")
    assert_refused(resp, 405, message="available at this address")
    assert resp.headers["allow"] == "POST"


def test_api_errors_are_json(ui):
    _, client, *_ = ui
    resp = client.get("/api/nowhere")
    assert resp.status_code == 404 and resp.json() == {"error": "Not found."}


# --- one error handler: no stack traces or upstream text in the browser ---


def test_unexpected_error_is_generic_and_logged(ui, monkeypatch, caplog):
    runner, client, *_ = ui

    def boom():
        raise RuntimeError("internal detail /data/recall-checker.db")

    monkeypatch.setattr(runner.db, "match_counts", boom)
    with caplog.at_level(logging.ERROR):
        resp = client.get("/?status=new")
    assert_refused(resp, 500, message="Something went wrong.")
    assert "internal detail" not in resp.text
    assert "internal detail" in caplog.text  # the log keeps it
    assert "Unhandled error on GET /" in caplog.text


def test_writeback_failure_shows_fixed_message_and_logs_detail(ui, monkeypatch, caplog):
    runner, client, match_id, _ = ui
    with runner.db.tx() as c:  # not yet written back, so confirming writes it
        c.execute("UPDATE matches SET writeback_at=NULL WHERE id=?", (match_id,))
    real = runner.homebox.client.request

    def homebox_down(method, url, **kw):  # Homebox answers 500 with its own text
        if method == "GET" and "/entities/" in url:
            return httpx.Response(500, text="upstream says: database is locked at /srv/hb.db")
        return real(method, url, **kw)

    monkeypatch.setattr(runner.homebox.client, "request", homebox_down)
    with caplog.at_level(logging.ERROR):
        resp = client.post(
            f"/match/{match_id}/status", data={"status": "confirmed"}, follow_redirects=False
        )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/match/{match_id}?notice=writeback-failed"
    page = client.get(resp.headers["location"])
    assert page.status_code == 200
    assert "Saved, but couldn&#39;t write to Homebox." in page.text  # autoescaped
    assert 'class="notice err"' in page.text
    assert "database is locked" not in page.text
    assert "database is locked" in caplog.text
    assert "hb_x" not in caplog.text  # the token is a header, never in the message
    row = runner.db.get_match(match_id)
    assert row["status"] == "confirmed" and row["writeback_at"] is None  # retried next run


# --- notices are codes; a link can't put its own text on the page ---


def test_notice_codes_map_to_fixed_text(ui):
    _, client, match_id, _ = ui
    page = client.get("/?notice=check-started")
    assert "Check started." in page.text and 'class="notice ok"' in page.text
    page = client.get(f"/match/{match_id}?notice=check-running")
    assert "A check is already running." in page.text


def test_test_notice_shows_the_servers_last_result_only(ui):
    _, client, *_ = ui
    assert 'class="notice' not in client.get("/?notice=test").text  # nothing sent yet
    resp = client.post("/test-notify", follow_redirects=False)
    assert resp.headers["location"] == "/?notice=test"  # no detail in the URL
    assert "Test notification: Sent to 1 target." in client.get("/?notice=test").text


def test_run_says_when_a_check_is_already_running(ui, monkeypatch):
    runner, client, *_ = ui
    started = []
    monkeypatch.setattr(runner, "run", lambda *a, **k: started.append(1))
    assert runner._lock.acquire(blocking=False)
    try:
        resp = client.post("/run", follow_redirects=False)
    finally:
        runner._lock.release()
    assert resp.headers["location"] == "/?notice=check-running"
    assert not started
    resp = client.post("/run", follow_redirects=False)
    assert resp.headers["location"] == "/?notice=check-started"


# --- links from the feeds ---


@pytest.mark.parametrize(
    "value, expected",
    [
        ("https://www.cpsc.gov/Recalls/2026/x", "https://www.cpsc.gov/Recalls/2026/x"),
        ("http://example.com/a", "http://example.com/a"),
        (" HTTPS://example.com/a ", "HTTPS://example.com/a"),
        ("javascript:alert(1)", ""),
        ("JaVaScRiPt:alert(1)", ""),
        ("data:text/html,<script>alert(1)</script>", ""),
        ("vbscript:x", ""),
        ("//evil.example/x", ""),
        ("/relative", ""),
        ("https://", ""),
        ("http://[::1", ""),  # unparseable
        ("", ""),
    ],
)
def test_http_url(value, expected):
    assert http_url(value) == expected


def test_non_http_recall_links_are_not_rendered(ui):
    runner, client, match_id, _ = ui
    recall = runner.db.get_recall(runner.db.get_match(match_id)["recall_key"])
    recall.url = "javascript:alert(document.domain)"
    recall.images = ["data:image/svg+xml,<svg onload=alert(1)>", "https://cdn.example/ok.jpg"]
    runner.db.upsert_recalls([recall])
    page = client.get(f"/match/{match_id}").text
    assert "javascript:" not in page and "data:image" not in page
    assert recall.label in page  # shown as text instead
    assert 'src="https://cdn.example/ok.jpg"' in page


def test_messages_drop_non_http_recall_links(ui):
    runner, _, match_id, _ = ui
    recall = runner.db.get_recall(runner.db.get_match(match_id)["recall_key"])
    recall.url = "javascript:alert(1)"
    entry = {
        "item": {"name": "Sleeper"},
        "recall": recall,
        "tier": Tier.high,
        "item_link": "http://homebox.test/item/i2",
        "match_id": match_id,
    }
    _, body = messages.notification([entry])
    assert "javascript:" not in body and recall.title in body
    assert "javascript:" not in messages.maintenance_text(recall, Tier.high)


# --- headers and request size ---


@pytest.mark.parametrize("path", ["/", "/static/style.css", "/nowhere", "/api/health"])
def test_security_headers_on_every_answer(ui, path):
    _, client, *_ = ui
    resp = client.get(path)
    csp = resp.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "default-src 'none'" in csp
    # Scripts only from this origin (the theme script): never inline, never another host.
    assert "script-src 'self';" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "same-origin"


def test_security_headers_on_guard_refusals(ui):
    _, client, *_ = ui
    resp = client.get("/", headers={"Host": "rebind.evil.example"})
    assert resp.status_code == 400 and "frame-ancestors" in resp.headers["content-security-policy"]


def test_templates_have_no_inline_script_or_style():
    # The CSP allows neither; an inline one would silently not apply. A script is a file under
    # /static/, loaded by src; inline event handlers (onclick=…) count as inline script.
    templates = Path(guards.__file__).parent / "templates"
    for page in templates.glob("*.html"):
        text = page.read_text()
        assert "style=" not in text and "<style" not in text, page.name
        assert not re.search(r"<script(?![^>]*\bsrc=\"/static/)", text), page.name
        assert not re.search(r"\son[a-z]+\s*=", text), page.name


def test_large_body_refused(ui):
    runner, client, match_id, _ = ui
    big = "x" * (guards.MAX_BODY_BYTES + 1)
    resp = client.post(f"/match/{match_id}/status", data={"status": "dismissed", "pad": big})
    assert resp.status_code == 413
    assert runner.db.get_match(match_id)["status"] == "new"


def test_large_body_without_length_refused(ui):
    runner, client, match_id, _ = ui

    def chunks():
        yield b"status=dismissed&pad="
        for _ in range(4):
            yield b"x" * guards.MAX_BODY_BYTES

    resp = client.post(
        f"/match/{match_id}/status",
        content=chunks(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 400 and "Traceback" not in resp.text
    assert runner.db.get_match(match_id)["status"] == "new"


# --- feed size caps ---


def test_cpsc_refuses_an_oversized_answer(monkeypatch):
    monkeypatch.setattr(cpsc, "MAX_RESPONSE_BYTES", 1000)
    with respx.mock() as router:
        router.get(CPSC).mock(return_value=httpx.Response(200, content=b"[" + b" " * 2000 + b"]"))
        with pytest.raises(FeedTooLarge):
            cpsc.CPSCSource(CPSC).fetch(None)


def test_cpsc_counts_bytes_when_length_is_missing(monkeypatch):
    monkeypatch.setattr(cpsc, "MAX_RESPONSE_BYTES", 1000)

    def stream():
        yield b"["
        yield b" " * 2000
        yield b"]"

    with respx.mock() as router:
        router.get(CPSC).mock(return_value=httpx.Response(200, content=stream()))
        with pytest.raises(FeedTooLarge):
            cpsc.CPSCSource(CPSC).fetch(None)


def test_cpsc_refuses_a_non_list_answer():
    with respx.mock() as router:
        router.get(CPSC).mock(return_value=httpx.Response(200, json={"error": "x"}))
        with pytest.raises(ValueError, match="list of recalls"):
            cpsc.CPSCSource(CPSC).fetch(None)


def test_cpsc_skips_non_object_records(raw_recalls):
    with respx.mock() as router:
        router.get(CPSC).mock(return_value=httpx.Response(200, json=[1, "x", raw_recalls[0]]))
        assert len(cpsc.CPSCSource(CPSC).fetch(None)) == 1


NHTSA_URL = "https://nhtsa.test/FLAT_RCL_POST_2010.zip"


def zipped(text: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("FLAT_RCL_POST_2010.txt", text)
    return buf.getvalue()


def test_nhtsa_refuses_an_oversized_download(monkeypatch):
    monkeypatch.setattr(nhtsa, "MAX_ZIP_BYTES", 100)
    with respx.mock() as router:
        router.get(NHTSA_URL).mock(return_value=httpx.Response(200, content=b"x" * 1000))
        with pytest.raises(FeedTooLarge):
            nhtsa.NHTSASource({"C"}, NHTSA_URL).fetch(None)


def test_nhtsa_refuses_a_zip_bomb(monkeypatch):
    monkeypatch.setattr(nhtsa, "MAX_TEXT_BYTES", 10_000)
    body = zipped(b"\n" * 100_000)  # tiny compressed, large unpacked
    assert len(body) < 10_000
    with respx.mock() as router:
        router.get(NHTSA_URL).mock(return_value=httpx.Response(200, content=body))
        with pytest.raises(FeedTooLarge, match="unpacks"):
            nhtsa.NHTSASource({"C"}, NHTSA_URL).fetch(None)


def test_nhtsa_zip_without_text_refused():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.md", b"nothing")
    with respx.mock() as router:
        router.get(NHTSA_URL).mock(return_value=httpx.Response(200, content=buf.getvalue()))
        with pytest.raises(ValueError, match="no .txt"):
            nhtsa.NHTSASource({"C"}, NHTSA_URL).fetch(None)


def test_nhtsa_skips_overlong_rows(monkeypatch):
    monkeypatch.setattr(nhtsa, "MAX_ROW_CHARS", 64)
    text = io.StringIO("short\n" + "y" * 500 + "\nnext\r\nlast", newline="")
    assert list(nhtsa.rows(text)) == ["short\n", "next\r\n", "last"]


def test_nhtsa_real_rows_pass_the_row_cap():
    sample = SAMPLE.read_bytes()
    with respx.mock() as router:
        router.get(NHTSA_URL).mock(return_value=httpx.Response(200, content=zipped(sample)))
        got = nhtsa.NHTSASource({"C"}, NHTSA_URL).fetch(None)
    assert {r.source_id for r in got} == {"26C006000", "26C003000", "26C002000"}
