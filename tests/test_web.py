from fastapi.testclient import TestClient

from recall_checker.web.app import create_app

from .test_runner import env  # noqa: F401  (fixture)


def test_pages_and_actions(env):  # noqa: F811
    runner, hb, _, router = env
    router.route(host="testserver").pass_through()
    runner.run()
    client = TestClient(create_app(runner))

    page = client.get("/")
    assert page.status_code == 200 and "Rock n Play Sleeper" in page.text

    assert client.get("/healthz").json()["ok"] is True

    sleeper = next(r for r in runner.db.list_matches() if r["item_id"] == "i2")
    detail = client.get(f"/match/{sleeper['id']}")
    assert detail.status_code == 200 and "Why this matched" in detail.text

    resp = client.post(f"/match/{sleeper['id']}/status", data={"status": "confirmed"},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert runner.db.get_match(sleeper["id"])["status"] == "confirmed"
    assert [t["name"] for t in hb.entities["i2"]["tags"]] == ["Recall"]

    client.post(f"/match/{sleeper['id']}/status", data={"status": "dismissed"})
    assert "Rock n Play" not in client.get("/?status=new").text
    assert client.get("/match/999999").status_code == 404


def test_test_notification_button(env):  # noqa: F811
    runner, _, notifier, router = env
    router.route(host="testserver").pass_through()
    client = TestClient(create_app(runner))

    page = client.post("/test-notify")
    assert "Test notification: Sent to 1 target." in page.text
    assert notifier.sent[-1][0] == "test"

    notifier.fail = True
    page = client.post("/test-notify")
    assert 'class="notice err"' in page.text and "Connection refused" in page.text
