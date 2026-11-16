from __future__ import annotations

import json
import logging
import threading
from pathlib import Path as FilePath
from typing import Annotated

from fastapi import FastAPI, Form, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException

from ..config import Tier, app_version, git_revision
from ..errors import AppError
from ..runner import Runner
from .guards import BodyLimit, CrossSiteGuard, HostGuard, SecurityHeaders

log = logging.getLogger(__name__)

HERE = FilePath(__file__).parent
STATUSES = ("new", "confirmed", "dismissed")
STATUS_FILTERS = (*STATUSES, "all")
TIER_FILTERS = ("", *(t.name for t in Tier))
# A health answer is about this moment: never from a cache.
NO_STORE = {"Cache-Control": "no-store"}
# SQLite's INTEGER is 64-bit; a larger id would raise inside the driver.
MAX_ID = 2**63 - 1
MatchId = Annotated[int, Path(ge=1, le=MAX_ID)]
NoticeCode = Annotated[str, Query(alias="notice")]

# What a redirect may ask a page to say. A link carries only the code, so no link can put its
# own text on the page. "test" shows the last test notification's result, held in memory.
NOTICES: dict[str, tuple[str, bool]] = {
    "check-started": ("Check started. Reload in a minute for the results.", True),
    "check-running": ("A check is already running.", True),
    "writeback-failed": (
        "Saved, but couldn't write to Homebox. The next check retries it; the container log "
        "has the details.",
        False,
    ),
}
TEST_NOTICE = "test"

# Fixed text for the statuses routing and the framework raise themselves. Their own detail is
# never shown: it can carry internals.
HTTP_MESSAGES = {
    400: "Bad request.",
    404: "Not found.",
    405: "That action isn't available at this address.",
}


def one_of(name: str, value: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        shown = ", ".join(a for a in allowed if a)
        raise AppError(f"Unknown {name}. Use one of: {shown}.")
    return value


def create_app(runner: Runner) -> FastAPI:
    db = runner.db
    app = FastAPI(title="Homebox Recall Checker", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    # No sign-in (README): refuse cross-site writes, and Host names that could be DNS rebinding.
    # Added last runs first: the headers go on every answer, an unknown Host is refused before
    # anything else, and a large body before any route reads it.
    app.add_middleware(BodyLimit)
    app.add_middleware(CrossSiteGuard, public_url=runner.s.public_url)
    names, any_ip = runner.s.host_rule()
    app.add_middleware(HostGuard, names=names, allow_any_ip=any_ip)
    app.add_middleware(SecurityHeaders)
    templates = Jinja2Templates(directory=HERE / "templates")
    # Read once: both are fixed for the life of the process.
    revision, version = git_revision(), app_version()
    templates.env.globals["Tier"] = Tier
    # The last test notification's (message, ok), shown by ?notice=test. Kept here rather than
    # in the redirect, so Apprise's detail never lands in a URL or the browser's history.
    last_test: dict[str, tuple[str, bool]] = {}

    # --- the one place errors become what the person sees ---
    def refusal(request: Request, status: int, message: str) -> Response:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": message}, status_code=status)
        return templates.TemplateResponse(
            request, "error.html", {"message": message, "status": status}, status_code=status
        )

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError) -> Response:
        return refusal(request, exc.status, exc.message)

    @app.exception_handler(RequestValidationError)
    async def bad_input(request: Request, exc: RequestValidationError) -> Response:
        # loc names one of this app's own parameters (path, query or form), never a value.
        fields = sorted({str(e["loc"][-1]) for e in exc.errors() if e.get("loc")})
        what = f": {', '.join(fields)} is missing or not valid" if fields else ""
        return refusal(request, 400, f"Bad request{what}.")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> Response:
        message = HTTP_MESSAGES.get(exc.status_code, "Request refused.")
        response = refusal(request, exc.status_code, message)
        if exc.headers:  # e.g. Allow on a 405
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(Exception)
    async def failure(request: Request, exc: Exception) -> Response:
        # The path only: the query string is the person's input.
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return refusal(request, 500, "Something went wrong. The container log has the details.")

    def notice(code: str) -> tuple[str, bool] | None:
        if not code:
            return None
        if code == TEST_NOTICE:
            return last_test.get(TEST_NOTICE)
        one_of("notice", code, (TEST_NOTICE, *NOTICES))
        return NOTICES[code]

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
    def index(request: Request, status: str = "new", tier: str = "", notice_code: NoticeCode = ""):
        one_of("status", status, STATUS_FILTERS)
        one_of("tier", tier, TIER_FILTERS)
        shown = notice(notice_code)
        min_tier = Tier[tier] if tier else Tier.low
        rows = db.list_matches(None if status == "all" else status, int(min_tier))
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "matches": [hydrate(r) for r in rows],
                "status": status,
                "tier": tier,
                "counts": db.match_counts(),
                "last_run": last_run(),
                "running": runner.running,
                "settings": runner.s,
                "notice": shown,
                "notifier_configured": runner.notifier.configured,
            },
        )

    @app.get("/match/{match_id}", response_class=HTMLResponse)
    def detail(request: Request, match_id: MatchId, notice_code: NoticeCode = ""):
        shown = notice(notice_code)
        row = db.get_match(match_id)
        if not row:
            raise AppError("No such match. It may have been removed.", 404)
        return templates.TemplateResponse(
            request,
            "match.html",
            {"m": hydrate(row), "settings": runner.s, "notice": shown},
        )

    @app.post("/match/{match_id}/status")
    def set_status(match_id: MatchId, status: Annotated[str, Form()]):
        one_of("status", status, STATUSES)
        if not db.get_match(match_id):
            raise AppError("No such match. It may have been removed.", 404)
        db.set_match_status(match_id, status)
        if status == "confirmed" and runner.s.writeback:
            try:
                runner.write_back_match(match_id)
            except Exception:
                # The status change stands, and the next check retries the write-back. The
                # detail (Homebox's answer) goes to the log, not the page.
                log.exception("Write-back failed for match %s", match_id)
                return RedirectResponse(
                    f"/match/{match_id}?notice=writeback-failed", status_code=303
                )
        return RedirectResponse(f"/match/{match_id}", status_code=303)

    @app.post("/run")
    def run_now():
        # Runner.run holds a lock, so a second start while one runs does nothing either way.
        if runner.running:
            return RedirectResponse("/?notice=check-running", status_code=303)
        threading.Thread(target=runner.run, daemon=True, name="manual-check").start()
        return RedirectResponse("/?notice=check-started", status_code=303)

    @app.post("/test-notify")
    def test_notify():
        ok, detail = runner.notifier.test(runner.s.public_url.rstrip("/"))
        last_test[TEST_NOTICE] = (f"Test notification: {detail}", ok)
        return RedirectResponse(f"/?notice={TEST_NOTICE}", status_code=303)

    # The container healthcheck and "what is running": no database, no Homebox, no feeds, so it
    # answers while a check runs and a slow dependency never restarts the container. Loopback
    # Hosts always pass HostGuard, and as a GET it is outside CrossSiteGuard.
    @app.get("/api/health")
    def health():
        return JSONResponse(
            {"ok": True, "revision": revision, "version": version}, headers=NO_STORE
        )

    # Deprecated: the health route before 0.3.0, kept with its old answer for anything that
    # still polls it, and removed in a later release (docs/DEPLOYMENT.md). It reads SQLite.
    @app.get("/healthz")
    def healthz():
        return JSONResponse(
            {"ok": True, "running": runner.running, "last_run": last_run()},
            headers={
                **NO_STORE,
                "Deprecation": "true",
                "Link": '</api/health>; rel="successor-version"',
            },
        )

    return app
