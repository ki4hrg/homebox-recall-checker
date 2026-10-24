"""Minimal client for the Homebox 0.26+ /v1/entities API."""
from __future__ import annotations

import logging
from collections.abc import Iterator

import httpx

log = logging.getLogger(__name__)

PAGE_SIZE = 100


class HomeboxError(RuntimeError):
    pass


def _is_zero_date(value: str | None) -> bool:
    # Homebox serialises unset dates as Go's zero time.
    return not value or value.startswith("0001-01-01")


def is_trackable(entity: dict) -> bool:
    """True for real items we should check: not locations, not archived, not sold."""
    etype = entity.get("entityType") or {}
    if etype.get("isLocation"):
        return False
    if entity.get("archived"):
        return False
    if not _is_zero_date(entity.get("soldDate")):
        return False
    return True


def slim_item(entity: dict) -> dict:
    """Keep only what matching, the UI and write-back need."""
    return {
        "id": entity["id"],
        "name": entity.get("name") or "",
        "description": entity.get("description") or "",
        "manufacturer": entity.get("manufacturer") or "",
        "modelNumber": entity.get("modelNumber") or "",
        "serialNumber": entity.get("serialNumber") or "",
        "purchaseDate": None if _is_zero_date(entity.get("purchaseDate"))
        else entity["purchaseDate"][:10],
        "updatedAt": entity.get("updatedAt") or "",
        "tags": [{"id": t["id"], "name": t.get("name", "")} for t in entity.get("tags") or []],
        "fields": [
            {"name": f.get("name") or "", "value": str(f.get("textValue") or "")}
            for f in entity.get("fields") or []
            if f.get("textValue")
        ],
        "location": (entity.get("location") or entity.get("parent") or {}).get("name", ""),
    }


class HomeboxClient:
    def __init__(self, base_url: str, token: str, client: httpx.Client | None = None):
        token = token.strip()
        if token and not token.lower().startswith("bearer "):
            token = f"Bearer {token}"
        self.client = client or httpx.Client(timeout=30.0)
        self.base = base_url.rstrip("/") + "/api/v1"
        self.headers = {"Authorization": token, "Accept": "application/json"}

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        resp = self.client.request(method, self.base + path, headers=self.headers, **kw)
        if resp.status_code >= 400:
            raise HomeboxError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")
        return resp

    def status(self) -> dict:
        return self._req("GET", "/status").json()

    def iter_entities(self) -> Iterator[dict]:
        """Yield every non-location entity summary, following pagination."""
        page = 1
        seen = 0
        while True:
            data = self._req(
                "GET", "/entities", params={"page": page, "pageSize": PAGE_SIZE}
            ).json()
            items = data.get("items") or []
            yield from items
            seen += len(items)
            if not items or seen >= (data.get("total") or 0):
                return
            page += 1

    def get_entity(self, entity_id: str) -> dict:
        return self._req("GET", f"/entities/{entity_id}").json()

    def list_tags(self) -> list[dict]:
        return self._req("GET", "/tags").json()

    def create_tag(self, name: str, description: str = "", color: str = "") -> dict:
        payload = {"name": name, "description": description}
        if color:
            payload["color"] = color
        return self._req("POST", "/tags", json=payload).json()

    def set_entity_tags(self, entity_id: str, tag_ids: list[str]) -> dict:
        return self._req(
            "PATCH", f"/entities/{entity_id}", json={"id": entity_id, "tagIds": tag_ids}
        ).json()

    def list_maintenance(self, entity_id: str) -> list[dict]:
        return self._req("GET", f"/entities/{entity_id}/maintenance").json() or []

    def add_maintenance(self, entity_id: str, name: str, description: str,
                        scheduled_date: str) -> dict:
        return self._req(
            "POST",
            f"/entities/{entity_id}/maintenance",
            json={"name": name, "description": description, "scheduledDate": scheduled_date},
        ).json()
