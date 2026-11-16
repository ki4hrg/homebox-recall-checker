# homebox-recall-checker

A Python service that checks a [Homebox](https://homebox.software) inventory against CPSC
consumer-product recalls and NHTSA child-seat and equipment recalls, notifies through Apprise,
writes a `Recall` tag and a maintenance entry back to Homebox, and lists matches in a small web
UI. FastAPI, APScheduler 3 and SQLite; Python 3.13 with uv. A public, released tool that other
Homebox users install. Follows https://github.com/ki4hrg/standards (Python service profile). The
`standards` plugin is enabled in `.claude/settings.json`; its `ship` skill is how work lands.
How it works: `docs/ARCHITECTURE.md` · deploy, releases and variables: `docs/DEPLOYMENT.md` ·
decisions: `docs/DECISIONS.md` · user docs: `README.md`.

## Commands

- `uv sync`: the dev environment in `.venv`, from `uv.lock`.
- `uv run ruff check && uv run ruff format --check && uv run pytest`: all three pass before
  anything is called done. CI runs the same.
- `uv run recall-checker check --dry-run`: one cycle against a real Homebox, with `.env` copied
  from `.env.example` (`Settings` reads `.env` from the working directory). Nothing is notified
  or written to Homebox, but recalls, items and matches are stored under `DATA_DIR`.
- After changing dependencies in `pyproject.toml`: `uv lock`, and commit both.
- A release: a PR that bumps `version` in `pyproject.toml`, then
  `gh release create vX.Y.Z --generate-notes` on that commit of `main` (`docs/DEPLOYMENT.md`).
  A merge alone deploys nothing.

## Rules that are easy to break

- **Bump a source's `parser_version`** (`sources/cpsc.py`, `sources/nhtsa.py`) when you change
  how it normalizes records. CPSC syncs incrementally and re-fetches everything only when its
  version changes, so without the bump old rows keep the old parsing. (NHTSA re-reads its whole
  file each sync anyway; bump it too, so the stored version says which parser wrote the rows.)
  A `--dry-run` also records the new version, so it uses up the full re-fetch.
- **A dismissed match stays dismissed.** `insert_match` only refreshes an existing pair's tier
  and evidence; it never resets `status`. Dismissed matches are never notified or written back
  (`db.pending_notifications`, `db.pending_writebacks`). Don't add a path that re-creates or
  re-opens them on a re-match.
- **Write-back is idempotent** (`writeback.py`): the tag is found by name (case-insensitive)
  and the maintenance entry by the recall's label before either is added, because the next
  check retries every match whose `writeback_at` is unset.
- **The recall index is rebuilt every run and dropped afterwards** (`Runner.sync_recalls`,
  `_release_memory`): it is about 150 MB and the hosts are small (1 GB is common). Don't cache
  it between runs.
- **Feed links become links only through `Recall.link` / `Recall.image_links`**, which drop
  anything that isn't an http(s) URL. Templates, notifications and write-back use those, never
  `recall.url` or `recall.images` directly.
- **What the person sees is fixed text.** Raise `AppError` (`errors.py`) for a refusal; the
  handlers in `create_app` turn everything else into a generic page and log the detail. No
  exception text, request value or Homebox/feed response reaches a page. The one exception is
  the test notification's failure reason (Apprise's own log lines, held in memory and shown by
  `/?notice=test`). Redirect notices are codes in `NOTICES`, never free text in the URL.
- **Every route goes through the middleware in `web/guards.py`** (no sign-in, by decision).
  A new route or method updates the route-guard table in `docs/ARCHITECTURE.md`; a new POST
  joins the cross-site refusal tests (the `web` fixture's `paths` in `tests/test_web.py`).
- **No inline `<script>`, `on…=` handler or `style=`** in templates: the CSP (`script-src
  'self'`, `style-src 'self'`) forbids them, and `tests/test_boundaries.py` checks it. Styles go
  in `web/static/style.css`, scripts in a file under `web/static/`.
- **Colours are tokens.** `style.css` defines the standard token names (`--color-page`,
  `--color-ink-3`…) once for light and twice, identically, for dark; rules use only those.
  `tests/test_design.py` checks every pair for AA contrast in both themes: a new text/background
  pair goes in its `TEXT_PAIRS`, a new colour token in `docs/DECISIONS.md` first.
- **Settings never echo a value.** A bad variable exits 1 with one `Error: …` line naming it;
  URLs and Apprise targets can carry credentials. `HOMEBOX_TOKEN` and `APPRISE_URLS` stay
  `repr=False`, and Apprise URLs are logged only as their scheme.
- **A new setting** goes in `Settings` (validated there), `.env.example`, the README's
  Configuration table, and `docs/DEPLOYMENT.md` if it is required or guarded.

## Layout

- `src/recall_checker/`
  - `__main__.py`: CLI (`serve`, `check [--dry-run]`, `test-notify`), wiring, scheduler
  - `config.py`: `Settings`, `Tier`, revision and version · `errors.py`: `AppError`
  - `runner.py`: one check cycle · `db.py`: SQLite schema and queries
  - `sources/`: the `RecallSource` protocol and `Recall` (`base.py`), `cpsc.py`, `nhtsa.py`
  - `matching.py`: `RecallIndex` and `Matcher` (tiers and evidence)
  - `homebox.py`: Homebox API client · `writeback.py`: tag and maintenance entry
  - `notify/`: the `Notifier` protocol and `apprise_.py` · `messages.py`: notification and
    maintenance text
  - `web/`: `app.py` (routes, error handlers), `guards.py` (middleware), `templates/`, `static/`
- `tests/`: pytest; Homebox and feeds faked with `respx` or in-process fakes, a real SQLite
  file per test; `fixtures/` holds CPSC and NHTSA samples
- `Dockerfile` · `compose.example.yml` (a snippet for users' Homebox stacks) · `.env.example`
  (every variable)
- `.github/workflows/`: `ci.yml` and `release.yml`, callers of the standards' workflows
- `docs/plans/standards-conformance.md`: the standards sweep · `docs/claude-candidates.md`:
  proposed rules, not binding

## Exceptions to the standards

Recorded in `docs/DECISIONS.md`: stays on the personal account, not the organization (public);
deploy mode B (versioned releases, no webhook or `deploy` job, revision checked by hand);
multi-arch release images; `compose.example.yml` is a user snippet, not a deployed stack; **no
sign-in in the web UI** (the network or the proxy's auth decides who reaches it, with
cross-site, `Host` and header guards in the app); `/api/health` also reports `version`, and
`/healthz` stays for now as a deprecated alias; Dependabot monthly though the repo is public.
