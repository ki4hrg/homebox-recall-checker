# Architecture

How the service works is in the README ("How it works"); this file holds what the standards ask
to keep next to the code. The rest of it (layout, data flow, the facts behind the code) is
written in the agent-docs phase (`docs/plans/standards-conformance.md`, Phase 4).

## Routes and guards

There is no sign-in (`docs/DECISIONS.md`, "No sign-in in the web UI"): who may use the UI is
decided by the network or the reverse proxy's auth in front of it. Every route is behind the
same middleware stack in `src/recall_checker/web/`, outermost first:

1. `SecurityHeaders` (`guards.py`): CSP (`default-src 'none'`, own stylesheet, http(s) images,
   `form-action 'self'`, `frame-ancestors 'none'`), `X-Frame-Options: DENY`, `nosniff`,
   `Referrer-Policy: same-origin`, on every answer, refusals included.
2. `HostGuard`: refuses (400) a `Host` not in `ALLOWED_HOSTS` (default: `PUBLIC_URL`'s host, any
   IP address, `localhost`), against DNS rebinding. Loopback always passes, for the healthcheck.
3. `CrossSiteGuard`: refuses (403) any method other than GET, HEAD and OPTIONS unless
   `Sec-Fetch-Site` is `same-origin` or absent and `Origin`, when sent, is this app's.
4. `BodyLimit`: refuses a request body over 16 KB (413 by `Content-Length`, 400 when it's
   absent and the body runs over).

| Route | Method | Authentication | Guards | Input parsing | Touches |
|---|---|---|---|---|---|
| `/` | GET | none (network or proxy) | Host | `status` ∈ `new` `confirmed` `dismissed` `all`; `tier` ∈ empty `low` `medium` `high`; `notice` ∈ the codes in `NOTICES` or `test`. Anything else: 400 | Reads SQLite: matches, items, recalls, last run. `notice=test` shows the last test-notification result held in memory |
| `/match/{match_id}` | GET | none | Host | `match_id` an integer 1 to 2⁶³−1 (400), unknown 404; `notice` as above | Reads SQLite: the item (serial number, location, purchase date) and the recall |
| `/match/{match_id}/status` | POST | none | Host, cross-site, body size | `match_id` as above; form `status` ∈ `new` `confirmed` `dismissed` (400); unknown match 404 | Writes the match status. `confirmed` with `WRITEBACK` on: a `Recall` tag and a maintenance entry in Homebox, with the API key. A failed write-back keeps the status, logs the detail and redirects with `notice=writeback-failed`; the next check retries it |
| `/run` | POST | none | Host, cross-site, body size | none | Starts a check in a thread unless one is running (`Runner.run` holds a lock): both feeds, Homebox reads, notifications, write-back |
| `/test-notify` | POST | none | Host, cross-site, body size | none | Sends a test message to every Apprise target; keeps the result in memory and redirects to `/?notice=test` |
| `/api/health` | GET | none | Host (loopback always passes) | none | Nothing: `{ ok, revision, version }` |
| `/healthz` (deprecated) | GET | none | Host | none | Reads SQLite: the last run |
| `/static/*` | GET | none | Host | Starlette's `StaticFiles` (stays inside the package's `static/`) | The stylesheet |

## Errors

`AppError` (`src/recall_checker/errors.py`) is a refusal the person can act on: its fixed message
and status are shown as they are. The web app's exception handlers (`create_app`) are the one
place errors become what the person sees: an `AppError`, a parameter that fails parsing (400,
naming the parameter, never its value), a routing error (404, 405) with fixed text, and anything
else as a logged 500 with a generic message. Pages get `error.html`; `/api/*` gets
`{ "error": message }`. No exception text, stack trace or upstream (Homebox, feed, Apprise)
response reaches the browser through them.

Notices after a redirect are codes (`?notice=check-started`), mapped to fixed text in `NOTICES`,
so a link can't put its own words on the page.

## Data from outside

- **Recall feeds** (CPSC JSON, NHTSA zipped flat file) are untrusted. Downloads are capped
  (CPSC 64 MB, NHTSA zip 128 MB, its text 1 GB declared size, rows 64 KB), so a broken or
  hostile server can't fill memory or disk; over a cap, that source's sync fails for the run and
  the others carry on. Links from them become `href`/`src` or notification links only when they
  are `http(s)` URLs (`Recall.link`, `Recall.image_links`); anything else is shown as text or
  dropped.
- **Homebox** answers are trusted as the user's own inventory, but its error text goes only to
  the log.
- **SQL** is parameterised throughout (`db.py`); the database is one file under `DATA_DIR`.
- **Templates** autoescape (`.html`), and none marks a value safe.
