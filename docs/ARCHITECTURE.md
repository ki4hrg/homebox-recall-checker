# Architecture

How the service is built. What it does for the person using it, the match tiers and every
variable: the README. Deploy, releases and operations: `docs/DEPLOYMENT.md`. Why things are the
way they are: `docs/DECISIONS.md`.

## Shape

- **One process, two threads of work:** `recall-checker serve` starts an APScheduler
  `BackgroundScheduler` that runs the check cycle, and uvicorn serving the FastAPI UI in the
  foreground. `recall-checker check [--dry-run]` runs one cycle and exits (1 on errors);
  `test-notify` sends one test message. All three print `recall-checker X.Y.Z, revision <sha>`
  to stderr before reading settings.
- **State:** one SQLite file, `DATA_DIR/recall-checker.db` (`/data` in the image).
- **Outbound only to:** Homebox (`HOMEBOX_URL`, API key in an `Authorization: Bearer` header),
  the two recall feeds over HTTPS, and the Apprise targets.
- **Plug points:** recall sources (`sources/base.py`, `RecallSource`: a `name`, a
  `parser_version` and `fetch(since)`) and notifiers (`notify/base.py`, `Notifier`) are
  protocols, so a new feed (FDA, Health Canada) or a native notifier doesn't touch the matcher.
  Sources are built from `RECALL_SOURCES` in `__main__.build_sources`.

| Module | Holds |
|---|---|
| `__main__.py` | CLI, wiring (`build_runner`), the scheduler and uvicorn |
| `config.py` | `Settings` (pydantic-settings, every variable validated at boot), `Tier`, `git_revision`, `app_version` |
| `runner.py` | `Runner`: the check cycle, its lock, and `write_back_match` used by the UI |
| `db.py` | Schema (created on start) and every query; one connection shared under a lock |
| `sources/` | `Recall`, `http_url`, `read_limited` and the protocol (`base.py`); `cpsc.py`, `nhtsa.py` |
| `matching.py` | `RecallIndex` (lookup tables) and `Matcher` (scoring, tiers, evidence) |
| `homebox.py` | Homebox `/api/v1` client, `is_trackable`, `slim_item` |
| `writeback.py` | `WriteBack.apply`: the `Recall` tag and the maintenance entry |
| `notify/` | `Notifier` protocol, `AppriseNotifier` |
| `messages.py` | Notification and maintenance-entry text |
| `errors.py` | `AppError` |
| `web/` | `app.py` (`create_app`: routes, error handlers, notices), `guards.py` (middleware), templates, stylesheet |

## The check cycle

`Runner.run(dry_run)` (`runner.py`). It takes a non-blocking lock: a second start while one runs
returns `None` and does nothing. The lock is per process: the scheduler and **Run check now**
share it, but `recall-checker check` (say, through `docker exec`) builds its own `Runner` and
can overlap a scheduled run. A failed source sync or a failed write-back of one match is
recorded in the run's `errors` and the run carries on; a failure anywhere else (Homebox down
during the item sync, for instance) ends the run there and skips the later steps. The run's
summary (`RunResult`) is stored in `meta.last_run`, which the UI shows.

1. **Sync recalls** (`sync_recalls`), per source, each in its own `try` so one feed being down
   doesn't stop matching against the others:
   - **CPSC** (`saferproducts.gov` JSON API). The first sync, or one after its
     `parser_version` changed, fetches every recall (`RecallDateStart=1900-01-01`); later ones
     fetch `LastPublishDateStart` = the last sync date minus two days of overlap.
     `normalize` pulls brands out of the firm names and title and records no models (CPSC
     rarely fills its structured field); `RecallIndex` finds model tokens in the text later.
   - **NHTSA** (the zipped flat file `FLAT_RCL_POST_2010.zip`). There is no date filter, so
     every sync downloads the whole file, keeps the rows of the types in `NHTSA_TYPES` (default
     child seats and equipment) and groups them into one `Recall` per campaign, with listed
     models and part numbers and the manufacture-date window.
   - `db.upsert_recalls` compares each recall's stored JSON with the new one and writes only
     what changed, since NHTSA amends campaigns without moving any date. `meta` keeps
     `<source>_last_sync` and `<source>_parser_version`.
   - Then the whole recall table is loaded into a fresh `RecallIndex`.
2. **Sync items** (`sync_items`): pages through Homebox's `/v1/entities` (100 a page). Locations,
   archived and sold items are skipped (`is_trackable`). An item's details are fetched only
   when its `updatedAt` differs from the stored one, and stored slimmed (`slim_item`: name,
   description, manufacturer, model and serial number, purchase date, tags, text custom fields,
   location). Items no longer listed are marked inactive, which keeps their matches out of
   notifications and write-back.
3. **Match** (`match`): `Matcher.match_item` for every active item, then `db.insert_match`.
   A new (item, recall) pair is inserted as `new`; an existing pair only gets its tier, score
   and evidence refreshed, so a confirmed or dismissed match keeps its status.
4. **Notify** (`notify`), skipped on a dry run: every match not yet notified, not dismissed, on
   an active item and at or above `MIN_NOTIFY_TIER` goes into **one** Markdown message (the
   first 20 listed, then a count and a link to the UI). Matches are marked notified only if
   Apprise reports success, so a failed delivery is retried next run. No Apprise target: a
   warning, nothing marked.
5. **Write back** (`write_back_pending`), skipped on a dry run or with `WRITEBACK=false`: every
   match not yet written back, not dismissed, on an active item, and either at or above
   `AUTO_WRITEBACK_TIER` or confirmed in the UI. `WriteBack.apply` re-reads the entity, adds the
   tag (`RECALL_TAG_NAME`, created red if missing, found by name case-insensitively) unless it's
   there, and adds a maintenance entry named after the recall (`CPSC Recall #…`) with the
   hazard, remedy, contact, affected models and links, unless one by that name exists. Then
   `writeback_at` is set. A failure is logged per match and retried next run.
6. **Release memory** (`finally`): the index is dropped, `gc.collect()` and glibc's
   `malloc_trim(0)` hand the heap back to the OS. The index is about 150 MB for the full set
   and the service idles between runs, so it is rebuilt each run (a few seconds) rather than
   kept. A full check peaks at about 190 MB.

A dry run still syncs and stores recalls, items and matches, and moves `<source>_last_sync`,
`<source>_parser_version` and `last_run` forward: only steps 4 and 5 are skipped. So a dry run
after a `parser_version` bump uses up that bump's full re-fetch.

Matches are never deleted. A pair that a later scoring or parser change no longer finds keeps
its old row, tier and status; a pair found again with a higher tier can become due for
notification or auto write-back on that run.

## Matching

`RecallIndex` prepares each recall once per run: model-like tokens from its title, product
names and description; normalized UPCs; brand token tuples; title and product-name words; and
the source's structured models (`struct`, NHTSA's). It keeps reverse maps by model, UPC and word,
so `Matcher` scores only the candidates an item shares a model, UPC or brand/name word with.

`Matcher._score` gives each candidate a tier (the signals are in the README's tier table) and a
list of `Evidence` (`signal`, `detail`, `snippet`) stored with the match and shown in the UI:

- **UPC** from a custom field named like `UPC`, `EAN`, `GTIN` or `Barcode`, or a 12- or
  13-digit number in the description: high.
- **Listed model or part number** (NHTSA), in the item's Model Number or inside its name: high
  with a brand match. Without one, medium when the Model Number field is model-like or the code
  found in the name is 8 characters or more. A digit-free "model" found in the name needs the
  brand and is medium at most.
- **Model in the recall's text**: high with a brand match, medium without. The item's Model
  Number field, when set, is used instead of model-like words in its name. Specs (`40V`,
  `4.0Ah`, `1200W`) never count as models in recall text or an item name; a Model Number of
  four characters or more is kept as typed.
- **With a brand match only:** a product line (IKEA "MALM") or a model variant/prefix is medium;
  every distinctive word of the item name (at least two) in the recall is medium unless the
  recall lists models and the item has one; at least half of them shared, one of four letters
  or more, is low.
- The brand matches when the item's Manufacturer matches a recalling firm or all its words
  appear in the recall's product title and product names. With no Manufacturer, or a
  placeholder one (`GENERIC_BRANDS`: "generic", "unknown", "unbranded" and the like), it
  matches when a firm's name is in the item name. But candidates are looked up by the item
  name's words only when the Manufacturer is empty (`Matcher._candidates`): an item with a
  placeholder Manufacturer reaches a recall only through a shared model or UPC.
- **Purchase date** caps the match at low: bought more than `PURCHASE_GRACE_DAYS` after the
  recall date, or before the first affected unit was made (with 30 days of slack).

`tests/test_matching.py` holds the false positives found in real inventories; a change to the
scoring keeps them passing.

## Storage

`db.py`, SQLite in WAL mode, created with `CREATE TABLE IF NOT EXISTS` on every start (no
migrations; a new column needs its own plan). One connection, shared by the scheduler thread,
the manual-check thread and the web handlers, under an `RLock`. All SQL is parameterised.

| Table | Holds | Rebuilt from outside? |
|---|---|---|
| `recalls` | `key` (`<source>:<id>`), the normalized recall as JSON | Yes, from the feeds |
| `items` | Slimmed Homebox items as JSON, `updated_at`, `active` | Yes, from Homebox |
| `matches` | One row per (item, recall): tier, score, evidence, `status` (`new`, `confirmed`, `dismissed`), `notified_at`, `writeback_at` | **No**: statuses and notification history exist only here |
| `meta` | `<source>_last_sync`, `<source>_parser_version`, `last_run` | Yes (a missing sync date means a full fetch) |

## Scheduling

`CHECK_CRON` (a crontab line, in the container's `TZ`) or every `CHECK_INTERVAL_HOURS`, as one
APScheduler job with `max_instances=1` and `coalesce=True`, plus a one-off job at startup when
`RUN_ON_STARTUP` is on. **Run check now** starts `Runner.run` in a daemon thread. All three
meet the same lock, so the server's checks never overlap (a CLI `check` is a separate process).

## Configuration

`Settings` (`config.py`) reads the environment, and a `.env` file in the working directory when
there is one. Every value is validated before anything starts; a bad one exits 1 with one
`Error: …` line per problem, naming the variable, and never prints a value (a bad `CHECK_CRON`
quotes APScheduler's message, which may echo part of the line: not a secret). The full list is the
README's Configuration table; `.env.example` shows each variable; `docs/DEPLOYMENT.md` lists what
is required and how compose guards it.

## UI

Server-rendered Jinja templates (`index.html`: the match list with status and tier filters and
the last run; `match.html`: one match, its evidence and the Confirm / Dismiss / Reopen form;
`error.html`), one stylesheet, no script. Confirming a match writes it back at once when
`WRITEBACK` is on (`Runner.write_back_match`); a failure keeps the status and the next check
retries. Dismissing or reopening removes nothing already written to Homebox, and
`writeback_at` and `notified_at` stay set, so a match reopened and confirmed again is neither
written nor notified a second time. `WriteBack` caches the tag's id for the life of the
process: a tag deleted in Homebox needs a restart. The maintenance entry is matched by its
exact name.

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
| `/test-notify` | POST | none | Host, cross-site, body size | none | Sends a test message to every Apprise target; keeps the result (with Apprise's reason on a failure) in memory and redirects to `/?notice=test` |
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
response reaches the browser through them. The guards in `guards.py` answer their own
refusals (400, 403, 413) as short fixed plain text, before any route or handler runs.

The one deliberate exception is the test notification: on a failure, `AppriseNotifier.test`
returns up to three of Apprise's own log lines (Apprise masks credentials in them, but a target
host name can appear), and `/?notice=test` shows them, so the person can see why delivery
failed. They are held in memory, never put in a URL.

Notices after a redirect are codes (`?notice=check-started`), mapped to fixed text in `NOTICES`,
so a link can't put its own words on the page.

## Data from outside

- **Recall feeds** (CPSC JSON, NHTSA zipped flat file) are untrusted. Downloads are capped
  (CPSC 64 MB, NHTSA zip 128 MB, its text 1 GB declared size), so a broken or hostile server
  can't fill memory or disk; over a cap, that source's sync fails for the run and the others
  carry on. NHTSA rows over 64K characters are skipped with a warning. Links from them become `href`/`src` or notification links only when they
  are `http(s)` URLs (`Recall.link`, `Recall.image_links`); anything else is shown as text or
  dropped.
- **Homebox** answers are trusted as the user's own inventory, but its error text goes only to
  the log.
- **SQL** is parameterised throughout (`db.py`); the database is one file under `DATA_DIR`.
- **Templates** autoescape (`.html`), and none marks a value safe.
