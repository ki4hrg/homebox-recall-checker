from __future__ import annotations

import json
import threading
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import Tier
from ..runner import Runner

HERE = Path(__file__).parent
STATUSES = ("new", "confirmed", "dismissed")


def create_app(runner: Runner) -> FastAPI:
    db = runner.db
    app = FastAPI(title="Homebox Recall Checker", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals["Tier"] = Tier

    def last_run() -> dict | None:
        raw = db.get_meta("last_run")
        return json.loads(raw) if raw else None

    def hydrate(row) -> dict:
        return {
            "id": row["id"],
            "tier": Tier(row["tier"]),
            "score": row["score"],
            "status": row["status"],
            "notified_at": row["notified_at"],
            "writeback_at": row["writeback_at"],
            "created_at": row["created_at"],
            "evidence": json.loads(row["evidence"]),
            "item": db.get_item(row["item_id"]) or {"id": row["item_id"], "name": "(removed)"},
            "item_link": runner.item_link(row["item_id"]),
            "recall": db.get_recall(row["recall_key"]),
        }

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, status: str = "new", tier: str = ""):
        min_tier = Tier.parse(tier) if tier else Tier.low
        rows = db.list_matches(status if status in STATUSES else None, int(min_tier))
        return templates.TemplateResponse(request, "index.html", {
            "matches": [hydrate(r) for r in rows],
            "status": status, "tier": tier, "counts": db.match_counts(),
            "last_run": last_run(), "running": runner.running,
            "settings": runner.s,
        })

    @app.get("/match/{match_id}", response_class=HTMLResponse)
    def detail(request: Request, match_id: int):
        row = db.get_match(match_id)
        if not row:
            raise HTTPException(404)
        return templates.TemplateResponse(request, "match.html", {
            "m": hydrate(row), "settings": runner.s,
        })

    @app.post("/match/{match_id}/status")
    def set_status(match_id: int, status: str = Form(...)):
        row = db.get_match(match_id)
        if not row or status not in STATUSES:
            raise HTTPException(400)
        db.set_match_status(match_id, status)
        if status == "confirmed" and runner.s.writeback:
            try:
                runner.write_back_match(match_id)
            except Exception as exc:  # surface but don't lose the status change
                raise HTTPException(502, f"Saved, but Homebox write-back failed: {exc}") from exc
        return RedirectResponse(f"/match/{match_id}", status_code=303)

    @app.post("/run")
    def run_now():
        if not runner.running:
            threading.Thread(target=runner.run, daemon=True, name="manual-check").start()
        return RedirectResponse("/", status_code=303)

    @app.get("/healthz")
    def healthz():
        return JSONResponse({"ok": True, "running": runner.running, "last_run": last_run()})

    return app
