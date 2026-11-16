import threading

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from recall_checker.config import Settings
from recall_checker.web.app import create_app

from .test_runner import env  # noqa: F401  (fixture)

# The fixture's PUBLIC_URL; the default ALLOWED_HOSTS admits its host name.
BASE = "http://checker.test"


def test_pages_and_actions(env):  # noqa: F811
    runner, hb, _, router = env
    router.route(host="checker.test").pass_through()
    runner.run()
    client = TestClient(create_app(runner), base_url=BASE)

    page = client.get("/")
    assert page.status_code == 200 and "Rock n Play Sleeper" in page.text

    assert client.get("/healthz").json()["ok"] is True

    sleeper = next(r for r in runner.db.list_matches() if r["item_id"] == "i2")
    detail = client.get(f"/match/{sleeper['id']}")
    assert detail.status_code == 200 and "Why this matched" in detail.text

    resp = client.post(
        f"/match/{sleeper['id']}/status", data={"status": "confirmed"}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert runner.db.get_match(sleeper["id"])["status"] == "confirmed"
    assert [t["name"] for t in hb.entities["i2"]["tags"]] == ["Recall"]

    client.post(f"/match/{sleeper['id']}/status", data={"status": "dismissed"})
    assert "Rock n Play" not in client.get("/?status=new").text
    assert client.get("/match/999999").status_code == 404


def test_test_notification_button(env):  # noqa: F811
    runner, _, notifier, router = env
    router.route(host="checker.test").pass_through()
    client = TestClient(create_app(runner), base_url=BASE)

    page = client.post("/test-notify")
    assert "Test notification: Sent to 1 target." in page.text
    assert notifier.sent[-1][0] == "test"

    notifier.fail = True
    page = client.post("/test-notify")
    assert 'class="notice err"' in page.text and "Connection refused" in page.text


CROSS_SITE = [
    {"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"},
    {"Sec-Fetch-Site": "same-site", "Origin": "https://other.checker.test"},
    {"Sec-Fetch-Site": "none"},
    {"Origin": "https://evil.example"},  # a browser without Fetch Metadata
    {"Origin": "https://checker.test.evil.example"},
    {"Origin": "http://checker.test:8081"},  # another port is another origin
    {"Origin": "null"},  # sandboxed frame, opaque origin
    {"Sec-Fetch-Site": "same-origin", "Origin": "https://evil.example"},
]
SAME_ORIGIN = [
    {"Sec-Fetch-Site": "same-origin", "Origin": BASE},
    {"Origin": BASE},
    {},  # curl, scripts: not a browser
]


@pytest.fixture
def web(env, monkeypatch):  # noqa: F811
    """A client plus a probe for each write path that says whether the write happened."""
    runner, hb, notifier, router = env
    router.route(host="checker.test").pass_through()
    runner.run()
    sleeper = next(r for r in runner.db.list_matches() if r["item_id"] == "i2")
    runs = []
    monkeypatch.setattr(runner, "run", lambda *a, **k: runs.append(1))

    def ran():
        for t in threading.enumerate():
            if t.name == "manual-check":
                t.join(5)
        return bool(runs)

    sent_before = len(notifier.sent)
    paths = {
        f"/match/{sleeper['id']}/status": (
            {"status": "dismissed"},
            lambda: runner.db.get_match(sleeper["id"])["status"] == "dismissed",
        ),
        "/run": (None, ran),
        "/test-notify": (None, lambda: len(notifier.sent) > sent_before),
    }
    return runner, TestClient(create_app(runner), base_url=BASE), paths


@pytest.mark.parametrize("headers", CROSS_SITE)
@pytest.mark.parametrize("which", [0, 1, 2])
def test_cross_site_writes_refused(web, which, headers):
    _, client, paths = web
    path, (data, happened) = list(paths.items())[which]
    resp = client.post(path, data=data, headers=headers, follow_redirects=False)
    assert resp.status_code == 403
    assert resp.text == "Cross-site request refused."
    assert not happened()


@pytest.mark.parametrize("headers", SAME_ORIGIN)
@pytest.mark.parametrize("which", [0, 1, 2])
def test_same_origin_and_non_browser_writes_allowed(web, which, headers):
    _, client, paths = web
    path, (data, happened) = list(paths.items())[which]
    resp = client.post(path, data=data, headers=headers, follow_redirects=False)
    assert resp.status_code == 303
    assert happened()


def test_no_write_by_get(web):
    _, client, paths = web
    for path, (_, happened) in paths.items():
        assert client.get(path, follow_redirects=False).status_code == 405
        assert not happened()


def app_with(env, **settings):  # noqa: F811
    runner, *_, router = env
    router.route(host__regex=r".*").pass_through()
    runner.s = runner.s.model_copy(update=settings)
    return TestClient(create_app(runner), base_url=BASE)


def test_origin_compared_with_host_behind_tls_proxy(env):  # noqa: F811
    # The proxy terminates TLS and passes Host through: the app sees http, the browser https.
    client = app_with(env, public_url="", allowed_hosts="recalls.example.com")
    hdrs = {
        "Host": "recalls.example.com",
        "Sec-Fetch-Site": "same-origin",
        "Origin": "https://recalls.example.com",
    }
    assert client.post("/test-notify", headers=hdrs, follow_redirects=False).status_code == 303
    hdrs["Origin"] = "https://evil.example"
    assert client.post("/test-notify", headers=hdrs).status_code == 403


def test_origin_matches_public_url_when_proxy_rewrites_host(env):  # noqa: F811
    client = app_with(env, public_url="https://recalls.example.com/")
    hdrs = {"Host": "198.51.100.5:8080", "Origin": "https://recalls.example.com"}
    assert client.post("/test-notify", headers=hdrs, follow_redirects=False).status_code == 303
    hdrs["Origin"] = "http://recalls.example.com"  # the scheme counts against PUBLIC_URL
    assert client.post("/test-notify", headers=hdrs).status_code == 403


def test_unknown_host_refused_by_default(env):  # noqa: F811
    client = app_with(env)
    resp = client.get("/", headers={"Host": "rebind.evil.example"})
    assert resp.status_code == 400 and "ALLOWED_HOSTS" in resp.text
    assert client.get("/", headers={"Host": "checker.test"}).status_code == 200
    assert client.get("/", headers={"Host": "192.0.2.20:8080"}).status_code == 200
    assert client.get("/", headers={"Host": "[fd00::20]:8080"}).status_code == 200
    assert client.get("/", headers={"Host": "localhost:8080"}).status_code == 200
    assert client.get("/", headers={"Host": ""}).status_code == 400


def test_allowed_hosts_list_replaces_default(env):  # noqa: F811
    client = app_with(env, allowed_hosts="recalls.example.com, 192.0.2.20")
    assert client.get("/", headers={"Host": "recalls.example.com"}).status_code == 200
    assert client.get("/", headers={"Host": "192.0.2.20:8080"}).status_code == 200
    assert client.get("/", headers={"Host": "192.0.2.21:8080"}).status_code == 400
    assert client.get("/", headers={"Host": "checker.test"}).status_code == 400


def test_allowed_hosts_star_turns_check_off(env):  # noqa: F811
    client = app_with(env, allowed_hosts="*")
    assert client.get("/", headers={"Host": "anything.example"}).status_code == 200


@pytest.mark.parametrize("allowed", ["", "recalls.example.com", "*"])
def test_healthcheck_still_ok(env, allowed):  # noqa: F811
    # The Dockerfile HEALTHCHECK calls http://127.0.0.1:8080/healthz.
    client = app_with(env, allowed_hosts=allowed)
    resp = client.get("/healthz", headers={"Host": "127.0.0.1:8080"})
    assert resp.status_code == 200 and resp.json()["ok"] is True


@pytest.mark.parametrize(
    "bad", ["https://recalls.example.com", "recalls.example.com:443", "recalls.example.com/ui"]
)
def test_allowed_hosts_rejects_urls(bad):
    with pytest.raises(ValidationError):
        Settings(allowed_hosts=bad, _env_file=None)
