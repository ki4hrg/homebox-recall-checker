import json

import httpx
import pytest
import respx

from recall_checker.config import Settings, Tier
from recall_checker.db import DB
from recall_checker.homebox import HomeboxClient
from recall_checker.runner import Runner
from recall_checker.sources.cpsc import CPSCSource

HB = "http://homebox.test"
CPSC = "https://cpsc.test/Recall"


class FakeNotifier:
    configured = True

    def __init__(self):
        self.sent: list[tuple[str, str]] = []
        self.fail = False

    def send(self, title, body):
        if self.fail:
            return False
        self.sent.append((title, body))
        return True

    def test(self, link=""):
        ok = self.send("test", link)
        return ok, "Sent to 1 target." if ok else "Delivery failed. Connection refused."


def entity(id, name, manufacturer="", model="", updated="2026-01-01T00:00:00Z", **kw):
    return {"id": id, "name": name, "manufacturer": manufacturer, "modelNumber": model,
            "updatedAt": updated, "tags": [], "fields": [], "archived": False,
            "soldDate": "0001-01-01T00:00:00Z", "purchaseDate": "0001-01-01T00:00:00Z",
            "entityType": {"isLocation": False}, **kw}


class FakeHomebox:
    """In-memory Homebox mounted on respx routes."""

    def __init__(self, router: respx.MockRouter, entities: list[dict]):
        self.entities = {e["id"]: e for e in entities}
        self.tags: list[dict] = []
        self.maintenance: dict[str, list[dict]] = {}
        self.detail_calls = 0
        r = router
        r.get(f"{HB}/api/v1/entities").mock(side_effect=self._list)
        r.get(url__regex=rf"{HB}/api/v1/entities/(?P<id>[^/]+)$").mock(side_effect=self._get)
        r.patch(url__regex=rf"{HB}/api/v1/entities/(?P<id>[^/]+)$").mock(side_effect=self._patch)
        r.get(f"{HB}/api/v1/tags").mock(side_effect=lambda req: httpx.Response(200, json=self.tags))
        r.post(f"{HB}/api/v1/tags").mock(side_effect=self._create_tag)
        r.route(url__regex=rf"{HB}/api/v1/entities/(?P<id>[^/]+)/maintenance$").mock(
            side_effect=self._maint)

    def _list(self, request):
        page = int(request.url.params.get("page", 1))
        size = int(request.url.params.get("pageSize", 100))
        items = list(self.entities.values())
        chunk = items[(page - 1) * size: page * size]
        return httpx.Response(200, json={"items": chunk, "page": page, "pageSize": size,
                                         "total": len(items)})

    def _get(self, request, id):
        self.detail_calls += 1
        return httpx.Response(200, json=self.entities[id])

    def _patch(self, request, id):
        body = json.loads(request.content)
        self.entities[id]["tags"] = [t for t in self.tags if t["id"] in body["tagIds"]]
        return httpx.Response(200, json=self.entities[id])

    def _create_tag(self, request):
        tag = {"id": f"tag{len(self.tags) + 1}", **json.loads(request.content)}
        self.tags.append(tag)
        return httpx.Response(201, json=tag)

    def _maint(self, request, id):
        entries = self.maintenance.setdefault(id, [])
        if request.method == "POST":
            entries.append(json.loads(request.content))
            return httpx.Response(201, json=entries[-1])
        return httpx.Response(200, json=entries)


@pytest.fixture
def env(raw_recalls, tmp_path):
    with respx.mock(assert_all_called=False) as router:
        router.get(CPSC).mock(return_value=httpx.Response(200, json=raw_recalls))
        hb = FakeHomebox(router, [
            entity("i1", "Laminator", "3M", "TL1302Z"),
            entity("i2", "Rock n Play Sleeper", "Fisher-Price"),
            entity("i3", "Router", "Netgear", "R7000"),
            entity("i4", "Old laminator", "3M", "TL1302Z", archived=True),
            entity("loc", "Garage", entityType={"isLocation": True}),
        ])
        settings = Settings(homebox_url=HB, homebox_token="hb_x", cpsc_base_url=CPSC,
                            data_dir=tmp_path, public_url="http://checker.test",
                            min_notify_tier=Tier.medium, auto_writeback_tier=Tier.high)
        notifier = FakeNotifier()
        runner = Runner(settings, DB(tmp_path / "t.db"), CPSCSource(CPSC),
                        HomeboxClient(HB, "hb_x"), notifier)
        yield runner, hb, notifier, router


def test_full_cycle_notifies_and_writes_back_once(env):
    runner, hb, notifier, _ = env
    res = runner.run()
    assert not res.errors
    assert res.items_total == 3              # archived item and location skipped
    assert res.recalls_total == 10
    assert len(notifier.sent) == 1           # one batched message
    title, body = notifier.sent[0]
    assert "TL1302Z" not in title and "Laminator" in body and "Rock n Play" in body
    assert "http://checker.test/match/" in body

    # high-tier laminator match written back; medium sleeper match not auto-written
    assert [t["name"] for t in hb.entities["i1"]["tags"]] == ["Recall"]
    assert hb.maintenance["i1"][0]["name"] == "CPSC Recall #27024"
    assert "Remedy:" in hb.maintenance["i1"][0]["description"]
    assert not hb.entities["i2"]["tags"]

    # second run: nothing new to say or write, and item details are served from cache
    calls = hb.detail_calls
    res2 = runner.run()
    assert res2.new_matches == 0 and res2.notified == 0 and res2.written_back == 0
    assert len(notifier.sent) == 1
    assert len(hb.maintenance["i1"]) == 1
    assert hb.detail_calls == calls


def test_changed_item_is_refetched(env):
    runner, hb, _, _ = env
    runner.run()
    calls = hb.detail_calls
    hb.entities["i3"]["updatedAt"] = "2026-02-01T00:00:00Z"
    res = runner.run()
    assert res.items_refreshed == 1
    assert hb.detail_calls == calls + 1


def test_dismissed_not_notified_and_confirm_writes_back(env):
    runner, hb, notifier, _ = env
    notifier.fail = True
    runner.run()                             # delivery fails -> retried later
    sleeper = [r for r in runner.db.list_matches() if r["item_id"] == "i2"]
    for r in sleeper:
        runner.db.set_match_status(r["id"], "dismissed")
    notifier.fail = False
    runner.run()
    assert len(notifier.sent) == 1
    assert "Rock n Play" not in notifier.sent[0][1]

    runner.db.set_match_status(sleeper[0]["id"], "confirmed")
    runner.write_back_match(sleeper[0]["id"])
    assert [t["name"] for t in hb.entities["i2"]["tags"]] == ["Recall"]
    assert len(hb.tags) == 1                 # existing tag reused


def test_dry_run_has_no_side_effects(env):
    runner, hb, notifier, _ = env
    res = runner.run(dry_run=True)
    assert res.new_matches > 0
    assert notifier.sent == [] and hb.tags == [] and hb.maintenance == {}


def test_removed_item_deactivated(env):
    runner, hb, notifier, _ = env
    notifier.fail = True
    runner.run()
    del hb.entities["i2"]
    notifier.fail = False
    runner.run()
    assert "Rock n Play" not in notifier.sent[0][1]


def test_incremental_recall_sync_uses_last_publish_date(env):
    runner, _, _, router = env
    runner.run()
    runner.run()
    cpsc_calls = [c.request for c in router.calls if c.request.url.host == "cpsc.test"]
    assert "RecallDateStart" in cpsc_calls[0].url.params
    assert "LastPublishDateStart" in cpsc_calls[1].url.params
