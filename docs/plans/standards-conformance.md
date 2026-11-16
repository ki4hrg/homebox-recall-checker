# Standards conformance: homebox-recall-checker

Audited 2026-11-16 against the standards at `v1` (`31fe30e`); the local standards clone is at
`0db4041` (docs past `v1`). Profile: **Python service** (`docs/profiles/python-service.md`), deploy
**mode B** (a service embedded in another stack, released on version tags). Template
`templates/python-service/` (only `.github/workflows/ci.yml`). This is the Phase 0 report,
committed with the first PR after the audit.

Repo `ki4hrg/homebox-recall-checker` is **public** and stays under the `ki4hrg` account (John's
decision, 2026-11-16: it is not moving into the organization). `main` = `571a459` ("Release
0.2.1"), no other branches, no PRs or issues ever opened. Nothing was run against Homebox, the
recall feeds, the notification services or the deploy host.

## Status

| Phase | Status | PR | Merge |
|---|---|---|---|
| 0 Audit | done (this file) | | |
| Hotfix: cross-site writes and unknown hosts (Q1, Q2) | done, released 0.2.2 | #1 | `5e8f677` |
| 1 Pipeline (+ uv, ruff format, Dependabot) | in review | `ci/standard-pipeline` | merge commit, by hand (Q6) |
| 2 Runtime (health, revision, config) | | | |
| 3 Boundaries (UI input, errors) | | | |
| 4 Agent docs | | | |
| 5 Design/copy | proposed, small (Q13) | | |
| 6 Stack | out (Q12) | | |

## The audit

- **What it is:** a FastAPI + APScheduler service (`src/recall_checker/`, about 2,000 lines) that
  mirrors CPSC recalls and NHTSA child-seat/equipment recalls into SQLite under `/data`, pages
  through Homebox items with an API key, matches them (high/medium/low tiers), notifies through
  Apprise, and writes a `Recall` tag plus a maintenance entry back to Homebox. A small
  server-rendered web UI (3 Jinja templates) lists matches and lets anyone who reaches it
  confirm, dismiss or reopen a match, start a check and send a test notification. CLI:
  `recall-checker serve | check [--dry-run] | test-notify`.
- **Activity:** 9 commits, all between 2026-10-24 and 2026-10-26 (inside the last 90 days), all
  pushed straight to `main` by John with a Claude co-author trailer. No PRs. Four releases
  (`v0.1.0` to `v0.2.1`), each with its own CI run.
- **Deploy today:** mode B, by design (`4b04ad3` dropped the Dockhand webhook job because the
  service lives inside the Homebox stack, which isn't a git stack). CI builds `linux/amd64` and
  `linux/arm64` under QEMU (the deploy host is arm64, `282ceff`) and pushes
  `ghcr.io/ki4hrg/homebox-recall-checker`: `:main` and `:sha-…` on every push to `main`;
  `:X.Y.Z`, `:X.Y` and `:latest` on a published release, after checking the tag against
  `pyproject.toml`. The compose example pulls `:latest` with `pull_policy: always`, and
  Dockhand's auto-update rolls each release out. The GHCR package is public (an anonymous pull
  of `:latest`'s manifest answers 200).
- **Repo settings (read with `gh api`, GET only):** public; merge commit, rebase and squash all
  allowed; `delete_branch_on_merge` false; no branch protection, no rulesets; default labels only
  (no `automerge`, `agent-found`); Dependabot alerts **off** (404), Dependabot security updates,
  secret scanning and push protection **off**; no Actions secrets or variables; Actions enabled;
  last runs (2026-10-27) started and passed. No LICENSE file (GitHub shows none) although
  `pyproject.toml` says `license = "MIT"`.
- **Checks run here** (a throwaway Python 3.13 venv outside the repo, removed afterwards):
  - `pytest`: **40 passed** on 3.13 (CI uses 3.12). One warning: Starlette deprecates `httpx`
    in its test client.
  - `ruff check src tests` (ruff 0.17.5, the repo's `E F I UP B` selection): clean.
  - `ruff format --check`: **17 files would be reformatted**. `python-check.yml` gates it.
  - `pip-audit` over the installed set (runtime and dev, latest versions resolved today:
    fastapi 0.163.1, starlette 1.10.0, uvicorn 0.54.0, httpx 0.28.1, apprise 2.2.0, jinja2
    3.1.6, python-multipart 0.0.34, apscheduler 3.11.3, pydantic-settings 2.16.3): **no known
    vulnerabilities**. Phase 1's audit gate should go green.
  - **Public-repo scan:** gitleaks 8.28.0 (official image, removed afterwards) over every commit
    on every ref and over the working tree: **no leaks**. The full patch history and commit
    messages were also searched for IP addresses, hostnames and LAN domains, emails and token
    prefixes, and the four release notes read. Findings below.

## Critical and security flags (read these first)

1. **Anyone who can reach the UI can change Homebox and silence recall alerts, and so can any web
   page a LAN user opens.** No route authenticates (`web/app.py`), and the README's answer is
   "keep it on your LAN or behind your reverse proxy's auth". What that exposes:
   - **Writes:** `POST /match/{id}/status` with `confirmed` writes the `Recall` tag and a
     maintenance entry into Homebox with the service's API key (`app.py:69–79`,
     `writeback.py`). `dismissed` silences a match for good: dismissed matches never notify and
     are never written back (`db.py:209, 218`), and for a car seat that is a safety alert
     switched off. `POST /run` starts a full check (two feed downloads and a ~190 MB peak);
     `POST /test-notify` sends a message to every Apprise target.
   - **Reads:** matched items' name, manufacturer, model, **serial number**, purchase date,
     location and description (`match.html`).
   - **CSRF:** the POSTs take a plain form with no token, no `Origin` or `Sec-Fetch-Site` check
     and no cookie, so a page on any site can submit them to the LAN address; match ids are small
     sequential integers. Browsers' private-network protections block some of this, not
     reliably.
   - **DNS rebinding:** no `Host` allowlist (no `TrustedHostMiddleware`), so a page that rebinds
     its own name to the LAN address can also *read* the UI.
   - **Held, not exposed:** the Homebox API key and the Apprise URLs (which carry their own
     credentials) live only in the environment. No template renders them; `settings` is passed
     to templates but only `settings.writeback` is used.
   - **Reach:** `compose.example.yml:31` publishes `"8080:8080"` on every interface.

   `security.md` says each entry point authenticates itself; the standards have no exception for
   a LAN-only tool or for auth delegated to a proxy (standards gap 3). This needs John's call
   (Q1, Q2). Whatever he chooses, the CSRF and `Host` checks are cheap and close the "any web
   page" path. **Answered:** no sign-in in the app (an exception), and the CSRF and `Host`
   checks plus the compose port binding ship first as a hotfix, released as 0.2.2 (Q1, Q2).
2. **Images install unpinned dependencies.** There's no lockfile and the Dockerfile runs
   `pip install .`, so each build resolves the newest fastapi, starlette, httpx, apprise… at
   that moment, untested by CI's own run (which resolves separately) and never audited. Lower
   bounds only (`fastapi>=0.115`, `apprise>=1.9`, `httpx>=0.27`); the profile asks for an upper
   bound on any dependency whose API has changed before. **Phase 1** (uv, `uv.lock`,
   `uv sync --locked --no-dev` in the image).
3. **Nothing watches dependencies.** No `dependabot.yml`, no audit in CI, alerts and security
   updates off. **Phase 1.**
4. **No secret scanning on a public repo.** GitHub secret scanning and push protection are off
   (free on public repos), and GitGuardian has never reported on this repo: there have been no
   PRs, and no app check appears on `main`'s commits. **Phase 1** (`repo-settings.sh`; check a
   GitGuardian check shows on the first PR).
5. **Every change went straight to `main`.** CI runs on `pull_request` ✓ and `image` waits for
   `test` ✓, but no change has gone through a PR, there's no merge-on-green, and the release job
   publishes `:latest` (what every install auto-updates to) from whatever `main` holds. **Phase 1.**
6. **No secret guard.** `HOMEBOX_TOKEN: ${HOMEBOX_RECALL_TOKEN}` (`compose.example.yml:15`) has no
   `:?`. A missing token boots, exits with a message (`__main__.py:35`) and restarts in a loop
   under `restart: unless-stopped`. **Phase 2.**

**Public-repo scan result: clean.** No credential, internal IP, LAN hostname, private repo name
or topology in tracked files, history, commit messages or release notes. Only placeholders
(`hb_xxxxxxxxxxxxxxxx`, `*.example.com`, `127.0.0.1`), public agency and manufacturer domains,
and manufacturer recall contact addresses copied from CPSC data in `tests/fixtures/`. Two
low-sensitivity notes, neither a leak: commit messages `282ceff` and `8c30f0f` say the deploy host
is arm64 with 1 GB of memory (no name or address); and `tests/test_matching.py:84–117` look like
real household items with purchase dates ("fix false positives from real data", `f25d789`), no
serial numbers (Q11). The commit author is the account's GitHub noreply address.

## Summary

| Area | Status | Top gaps |
|---|---|---|
| Workflow | partial | All 9 commits pushed to `main`; hand-rolled `ci.yml` on `ubuntu-latest`, `actions/*@v4`/`@v5`; no merge-on-green, close-issues, `automerge`/`agent-found` labels, PR template; all three merge methods allowed, branches not deleted; commit subjects not Conventional Commits. Meets: CI on `pull_request`, `contents: read` at the top and `packages: write` only on `image`, release tag checked against `pyproject.toml` |
| Quality | partial | ruff ✓ (clean), pytest ✓ (40 tests, real SQLite file via `tmp_path`, `respx` for HTTP); `ruff format` not applied (17 files); no typecheck (not in the Python profile); `/?tier=bogus` → 500 (`Tier.parse` raises `KeyError`); a failed write-back answers raw JSON with Homebox's response text |
| Security | partial | Unauthenticated UI that writes to Homebox, no CSRF or `Host` check (flag 1); no lockfile, no audit in CI, no Dependabot, alerts/security updates/secret scanning/push protection off; no compose guard. Meets: non-root container (uid 10001), env read by `pydantic-settings`, parameterised SQL, Jinja autoescape, timeouts on every HTTP client, token only in a header, `.env` gitignored, `.env.example` placeholders only |
| Deploy | partial (mode B) | Image built in CI ✓, no `build:` ✓, `pull_policy: always` on the mutable `:latest` ✓, `:main` + `:sha-` + semver tags ✓, OCI revision label ✓, Dockerfile `HEALTHCHECK` ✓, `PYTHONUNBUFFERED=1` ✓. Gaps: `/healthz` reads SQLite and reports no revision (no `/api/health`, no `GIT_REVISION`); arm64 built under QEMU (`deploy.md`: native arm runner); `image.yml@v1` can't publish release tags or a multi-arch image (standards gap 1); no `docs/DEPLOYMENT.md`. Webhook, Postgres, `PGPASSWORD`, migrations: **n/a** |
| Stack | differs | pip + hatchling, no `uv.lock` (profile: uv, locked); Python 3.12 in CI and the image (profile: 3.13); `[project.optional-dependencies] dev` (uv: `[dependency-groups]`); single-stage image without uv. Meets: `src/<package>/`, console script, `pydantic-settings`, ruff, pytest, `-slim` base, `apscheduler<4` bounded |
| Design | partial | Own CSS variables (`--bg`, `--fg`, `--card`…), not the standard names; dark mode by system preference only, no toggle; contrast fails in places (below); button and tab casing. Manifest: n/a (a service's admin page) |
| Agent docs | missing | No CLAUDE.md, AGENTS.md, `docs/`, `.claude/settings.json`, `claude-candidates.md`. README is thorough and current, except its Development section (pip/venv) and a `.env.example` with 5 of the 19 variables the README lists |

## Details

### Workflow

- `ci.yml` triggers on push to `main`, `pull_request` and `release: published` ✓. No
  `concurrency`, no `workflow_dispatch`, runners `ubuntu-latest` (standard `ubuntu-24.04`).
- `permissions: contents: read` at the top, `packages: write` only on `image` ✓. PR runs never
  build the image (it runs on push and release only), so a Dockerfile change is first built on
  `main`.
- **Versioning conforms.** `decisions.md` keeps tags for distributed artifacts, and the profile's
  "Released tools" covers a service pulled into another stack: `version` in `pyproject.toml`,
  `vX.Y.Z` tags, a CI check that they match, `gh release create --generate-notes`. This repo does
  all four. It's also published for other Homebox users (README). Keep it versioned.
- **Public repo:** minutes are free; the template's `runs-on` expression keeps it off the
  self-hosted runner; `PR_RUNNER`, `MAIN_RUNNER` and `IMAGE_RUNNER` stay unset.
- **Stays under `ki4hrg`:** the organization move (`decisions.md`) exists to share the
  self-hosted runner, which a public repo can't use anyway. Recorded as an exception. Same owner
  as the standards, so nothing about secret passing changes (there is no deploy job either).

### Quality

- `python-check.yml@v1` runs `uv sync --locked`, `ruff check`, `ruff format --check` and `pytest`
  with no way to leave one out, so Phase 1 must bring uv and the format commit (tests already
  exist and pass).
- Tests: `tests/test_web.py` drives the UI with FastAPI's `TestClient` and a fake Homebox;
  `test_runner.py` covers notification, write-back and dismissal; the database is a real SQLite
  file per test. No Postgres, so "tests on real Postgres" is n/a.
- Input handling in the UI: `match_id` is typed `int` ✓; `status` (form) is checked against
  `STATUSES` ✓; the `tier` query parameter is not (`app.py:49`): an unknown value raises a 500.
  `notice` is reflected into the page, autoescaped ✓.
- Error display: a write-back failure raises `HTTPException(502, f"…: {exc}")`
  (`app.py:77`), which a browser form shows as a bare JSON page carrying up to 300 characters of
  Homebox's response body. Not a secret (the key is in a header), but not the "one place turns
  errors into what the person sees" of Phase 3.
- Recall links (`r.url`, `r.images[0]`) come from the feeds and are rendered as `href`/`src`
  without a scheme check: harmless while the feeds are the agencies', but cheap to restrict to
  `https:`.

### Security

- `pip-audit`: clean today (above). `security.yml` audits from `uv.lock`, so it needs Phase 1's uv
  move.
- Dependabot: none. Ecosystems: `uv`, `docker`, `github-actions`.
- Compose guards: none (flag 6). `APPRISE_URLS` is optional (the app runs without notifications),
  so it stays `${…:-}`.
- Non-root ✓: `useradd --uid 10001`, `/data` created and handed to it before `USER`
  (`Dockerfile:11–14`). No host `chown` needed in Phase 2.
- Boot validation: `pydantic-settings` reads and types everything ✓, but `HOMEBOX_TOKEN` defaults
  to `""` and is checked later with `sys.exit`, an unknown `RECALL_SOURCES` entry exits the same
  way, and a malformed `CHECK_CRON` raises a traceback from APScheduler when `serve` starts. Phase 2
  moves these into the settings class so they fail as one readable error each.
- Secrets in logs: Apprise URLs are logged only as their scheme (`apprise_.py:16`) ✓. The test
  notification returns Apprise's own last log lines to the UI, through the redirect's query
  string (`app.py:86–89`); Apprise masks credentials in its log lines, but the target hostname can
  appear, and it lands in browser history. Low; Phase 3 can show it without putting it in the URL.
- Outbound requests: CPSC (`saferproducts.gov`) and NHTSA (`static.nhtsa.gov`) over HTTPS with
  timeouts ✓; Homebox over the stack network with a 30 s timeout ✓. Both feed URLs are
  configurable (`CPSC_BASE_URL`, `NHTSA_FLAT_FILE_URL`, not in the README).
- Workflow permissions: least-privilege already ✓.

### Deploy (mode B)

- **How it reaches the host:** a release publishes `:latest`; Dockhand's auto-update (or "Check
  for updates") pulls it into the Homebox stack. A merge to `main` publishes only `:main` and
  `:sha-…`, which nothing deployed pulls. So in mode B, "John watches the deploy" (Phases 2 and 3)
  means after the release that carries them, not after the merge.
- **Revision:** the image has the `org.opencontainers.image.revision` label ✓ (metadata-action),
  but no `GIT_REVISION`, and `/healthz` (`app.py:91–93`) reports `running` and the last run, read
  from SQLite. Phase 2 adds `/api/health` (no auth, no database, `{ ok, revision }`) and points the
  Dockerfile `HEALTHCHECK` at it. Whether anything outside the repo polls `/healthz` is Q9.
- **Architecture:** `linux/amd64,linux/arm64` under QEMU. `deploy.md` asks for arm64 built natively
  on `ubuntu-24.04-arm`. `image.yml@v1` builds one platform list in one job with no QEMU set up and
  tags only `:<branch>` and `:sha-…`, so it can't produce this repo's release tags or a
  multi-arch manifest (standards gap 1, Q3).
- **Compose:** `compose.example.yml` is a snippet for users to paste into their own Homebox stack,
  not a stack anything deploys, so the `compose.yaml` name and the git-stack rules don't apply
  (exception). It has `pull_policy: always` ✓, no `build:` ✓, no healthcheck (the image has one) ✓.
  Port `"8080:8080"` on every interface (flag 1; `deploy.md`: bind to the interface the proxy uses,
  or join the proxy's network).
- **Data:** SQLite at `/data/recall-checker.db`. Recalls and item snapshots are re-derivable from
  the feeds and Homebox; match statuses (confirmed, dismissed) and notification history are not.
  Covered by the host's nightly snapshot (`decisions.md`); `docs/DEPLOYMENT.md` says so.
- `docs/DEPLOYMENT.md` missing: mode B and the auto-update, arm64 host, exposure, backups,
  revision checked by hand from `/api/health` or the label.

### Stack

Migration work, not defects: uv with `uv.lock` (forced into Phase 1 by `python-check.yml`), dev
dependencies as a `[dependency-groups] dev` group, Python 3.13 (Q5), the image installing with
`uv sync --locked --no-dev` from uv's official image, upper bounds on `fastapi`, `apprise` and
`httpx` (Starlette went to 1.x and Apprise to 2.x under the current lower bounds). Layout,
console script, `pydantic-settings`, ruff and pytest already match.

### Design and copy (quick pass)

- **Contrast (WCAG AA 4.5:1 for normal text):** primary button in dark mode, white on `#7aa2ff`:
  **2.49**; `medium` badge, white on `#e08a00`: **2.69**; `low` badge, white on `#6b7d8f`:
  **4.24**; Dismiss button text `#c62828` on the dark card: **2.93**; the success notice
  `#2e7d32` on the dark page: **3.56**. Light mode passes (primary 6.47, `high` 5.62, muted 4.99).
- **Tokens:** `--bg`, `--fg`, `--muted`, `--card`, `--border`, `--accent`, `--high/medium/low`,
  `--ok`; the standard names (`page`, `card`, `accent`, `accent-ink`, status `ok`/`warn`/`danger`
  with `-ink`) aren't used. No Tailwind, so they'd be CSS custom properties (their spelling is an
  open question in the standards).
- **Dark mode:** system preference via `prefers-color-scheme` ✓, no light/dark/system toggle, so no
  flash to worry about yet.
- **Copy and casing:** buttons "Run check now", "Test notification", "Confirm — this is my item
  (tag in Homebox)", "Dismiss — not affected"; tabs and badges lower-case (`new`, `confirmed`,
  badges uppercased by CSS). Title Case and shorter labels would change about a dozen strings.
  Dismiss is reversible (Reopen), so it needs no confirm.
- Layout: works at phone width (wrapping header, `auto-fit` grid, `overflow-wrap: anywhere` on
  values) ✓; names wrap, nothing is truncated ✓. Manifest: n/a, an admin page in a browser tab.
- Nothing a user creates needs removing: matches come from the checker, and Reopen/Dismiss cover
  them.

### Agent docs

- No CLAUDE.md, AGENTS.md or `docs/`. The README already holds most of what `docs/ARCHITECTURE.md`
  needs (how it works, tiers, write-back, sources as protocols, `parser_version`) and the
  configuration table.
- README "Development" uses `python -m venv` and `pip install -e '.[dev]'`; it becomes `uv sync`
  / `uv run` in Phase 1. Its release snippet (`gh release create v0.2.0 …`) stays valid.
- `.env.example` lists 5 variables; the README lists 19, and the code reads three more
  (`CPSC_BASE_URL`, `NHTSA_FLAT_FILE_URL`, `UI_HOST`). Phase 4 makes them match.
- No plans referenced outside the repo ✓.

## CLAUDE.md triage

Nothing to triage: there is no CLAUDE.md or AGENTS.md, and no rules in `docs/`. Phase 4 writes a
CLAUDE.md from the code and README. Facts it should carry (not rules needing a decision): the
`parser_version` bump re-fetches a source in full; the index is rebuilt per run and released
afterwards, for memory (`8c30f0f`); dismissed matches never notify or write back; write-back is
idempotent; releases are cut by bumping `pyproject.toml` and `gh release create`. Rule-like ones
that come up go to `docs/claude-candidates.md`.

## Fix list (ordered by harm, rough effort)

1. **UI trust boundary** (S, the 0.2.2 hotfix, per Q1 and Q2): reject cross-site writes
   (`Origin`/`Sec-Fetch-Site`), `Host` allowlist (`ALLOWED_HOSTS`, defaulting to `PUBLIC_URL`'s
   host), the compose example's port binding, the no-sign-in stance in the README. The
   route-guard table stays in Phase 4's `docs/ARCHITECTURE.md`.
2. **Pinned, audited dependencies and the pipeline** (M, Phase 1): uv + `uv.lock`, image from the
   lockfile, template `ci.yml` (check, security, image, automerge, close-issues; mode B publishing
   per Q3), ruff format commit, Dependabot, repo settings and security features, PR template.
3. **Secret guard, boot validation, revision** (S, Phase 2): `${HOMEBOX_RECALL_TOKEN:?…}`;
   required token and parsed `CHECK_CRON`/`RECALL_SOURCES` in `Settings`; `/api/health` with
   `GIT_REVISION`; `docs/DEPLOYMENT.md`.
4. **Errors and input** (S, Phase 3): `tier` parsed to a 400; one handler turning Homebox and
   other failures into a readable page; `https:` check on feed links; test-notify detail out of
   the URL.
5. **Agent docs** (S, Phase 4).
6. **Contrast, tokens, toggle, copy** (S, Phase 5, Q13).

## Prerequisites before Phase 1

- The hotfix (0.2.2) merged and released.
- Q3 answered (how releases publish). If the answer is a standards change, it lands in the
  standards repo and `v1` moves first ("docs never get ahead of `v1`").
- GitGuardian: John's installation covers this repo (it's under his account, so it should;
  confirm a check appears on PR 1).
- No `PR_RUNNER` / `MAIN_RUNNER` / `IMAGE_RUNNER` on this repo (public).
- After PR 1 merges: `scripts/repo-settings.sh ki4hrg/homebox-recall-checker` (squash only,
  delete branch on merge, `automerge`/`agent-found` labels, alerts, security updates, secret
  scanning, push protection).

## Proposed scope and PR plan (to agree with John)

Scope: Phases 1–5; 6 out except what Phase 1 needs (uv, Python 3.13) (Q12, accepted). The repo
stays under `ki4hrg`, so nothing waits on a transfer. A hotfix PR first (Q2), then five PRs.
Releases: 0.2.2 after the hotfix; 0.3.0 after Phase 2, watched; 0.3.1 after Phase 3 (Q8).

| # | PR | Contents | Touches workflows | Merge |
|---|---|---|---|---|
| 0 | **Hotfix** `fix: refuse cross-site writes and unknown hosts` | Every non-GET/HEAD/OPTIONS request refused (403) unless `Sec-Fetch-Site` is `same-origin` or absent and `Origin`, when present, matches the `Host` header or `PUBLIC_URL`; `Host` allowlist `ALLOWED_HOSTS` (unset: `PUBLIC_URL`'s host, any IP literal, `localhost`; `*` off), the healthcheck's `127.0.0.1` always allowed; no `X-Forwarded-*` trusted; compose example port on `127.0.0.1`; README "Access and security" with the no-sign-in stance and the upgrade note; `.env.example`; refusal tests per write path; `version` 0.2.2; this plan | no | John by hand, then publishes release 0.2.2 |

| 1 | **Phase 1** `ci: standard pipeline with uv, locked image and Dependabot` | `ci.yml` from the template with no `deploy` job (mode B), `image` publishing per Q3, `release: published` kept with the tag/version check; `pyproject.toml` to `[dependency-groups]`, upper bounds, `requires-python >=3.13` + `.python-version` + `uv.lock`; Dockerfile `python:3.13-slim` with uv from its official image, `uv sync --locked --no-dev`; `ruff format` as its own commit, in `.git-blame-ignore-revs` (Q6); `.github/dependabot.yml` (`uv`, `docker`, `github-actions`; grouped; ignores Python image majors and `ki4hrg/standards*` majors) (Q10); PR template; `docs/claude-candidates.md`; `docs/DECISIONS.md` with the exceptions below; `LICENSE` (Q7); README Development section to uv; this plan | **yes** | **John by hand**. Then `repo-settings.sh`; check GitGuardian shows on the next PR; check the `:main` image's revision label is the merge commit |
| 2 | **Phase 2** `fix: report the running revision and refuse missing config` | `/api/health` (no auth, no DB, `{ ok, revision }`), `ARG`/`ENV GIT_REVISION`, `HEALTHCHECK` on it, `/healthz` removed (Q9: nothing polls it); `HOMEBOX_TOKEN` required and `CHECK_CRON`/`RECALL_SOURCES`/`NHTSA_TYPES` validated in `Settings`, one readable line per problem, no values echoed; compose example `${HOMEBOX_RECALL_TOKEN:?set HOMEBOX_RECALL_TOKEN}`; `docs/DEPLOYMENT.md` | no | `automerge` is off for Phase 2 (checklist): John merges, then publishes release 0.3.0 and watches the auto-update. Verify: `/api/health` reports the release commit, `id` is uid 10001, `/data` writable, an empty token fails `compose up` |
| 3 | **Phase 3** `fix: parse UI input and show errors readably` | (CSRF, `Host` allowlist and port binding moved to the hotfix, Q1/Q2) `tier`/`status` parsed with 400s; one error handler rendering a readable page for Homebox failures and a logged generic one for the rest; `https:` check on recall links; test-notify result kept out of the query string; route-guard table in `docs/ARCHITECTURE.md`; refusal tests; `/security-review` | no | `automerge`, then release 0.3.1 (Q8) |
| 4 | **Phase 4** `docs: agent docs` | CLAUDE.md (commands, layout, the facts above, exceptions); `docs/ARCHITECTURE.md` (from the README's "How it works", plus the route-guard table); `AGENTS.md` pointer; `.claude/settings.json` (standards plugin, `autoUpdate`); `.env.example` with every variable; fact-check by a fresh reviewer | no | `automerge` (docs-only: no image) |
| 5 | **Phase 5** `fix(ui): readable contrast, standard tokens and a theme toggle` | Standard token names as CSS custom properties; AA contrast in both themes (the five pairs above) with a contrast test; light/dark/system toggle with a classic `<head>` script; `ui-verbosity-reduction` and casing pass | no | `automerge`; ships in the next release |

**Why not fewer:** PR 1 changes workflows, so it's a hand merge anyway. PR 2 changes how the
container boots and is watched. The CSRF fix went ahead as the 0.2.2 hotfix (Q2), so Phase 3
is only input and errors and follows right after. Phases 4 and 5 are cheap separate merges on a public repo.

### Phases that don't apply

- **Deploy webhook, `APP_HEALTH_URL`, Force redeploy / Re-pull images:** n/a, mode B (no git stack).
- **Postgres, `PGPASSWORD`, migrations:** n/a, SQLite created by `executescript` on start.
- **Node 24:** n/a.
- **Self-hosted runner:** n/a, public repo.
- **Organization move:** not happening (John, 2026-11-16).

### Exceptions to record in `docs/DECISIONS.md` (if John agrees)

- Stays under the `ki4hrg` account, not the organization: public, so it can't use the runner the
  move is for.
- Deploy mode B: versioned releases, `:latest` pulled by the host's auto-update; no webhook.
- `compose.example.yml` is a user-facing snippet, not a deployed `compose.yaml`.
- Multi-arch image (amd64 and arm64): published for other people, deployed on arm64 (Q4).
- No sign-in in the app (Q1, decided 2026-11-16): it relies on the network or the reverse proxy's
  auth, with cross-site write and `Host` checks in the app and the compose port bound for a proxy.
- Revision checked by hand after a release (`/api/health` from the LAN, or the image label).

## Standards gaps

1. **`image.yml@v1` can't serve deploy mode B.** It tags only `:<branch>` and `:sha-…`, and the
   template pushes only on `main`, so the release tags the profile's "Released tools" calls for
   (`:X.Y.Z`, `:X.Y`, `:latest`) have no path through the reusable workflow. It also can't build a
   multi-arch manifest: no QEMU step, and two native jobs (amd64, arm64) would overwrite each
   other's tags without a manifest-merge step. A compatible `v1` change could add a `tags` input
   (extra metadata-action lines) and a push condition for `release`, plus either QEMU when
   `platforms` lists more than one, or a per-platform digest output and a merge job.
2. **The Python template promises mode B but shows only mode A.** Its header says "publish on
   version tags instead"; nothing shows how (`release` trigger, the tag/version check, which job
   pushes).
3. **No rule for a UI without sign-in.** `security.md` says every entry point authenticates; the
   standards don't say whether a single-user LAN tool may rely on the network or on the reverse
   proxy's auth, nor require CSRF (the Node profile's `Origin` check is tied to cookie sessions) or
   a `Host` allowlist against DNS rebinding for such a tool.
4. **"App repos move into the organization"** reads as every app repo; a public, released tool
   that stays on the personal account is an exception each time. The rule may want to say "repos
   that use the self-hosted runner".

The Python version rule, `ruff format` adoption, `pip-audit`'s threshold and the template's
missing files are already open in the standards' `open-questions.md`.

## John's answers (2026-11-16)

- **Q1:** (a). No sign-in in the app, recorded as an exception that relies on the network or the
  proxy's auth, plus the CSRF and `Host` checks and the compose port binding. Moved out of
  Phase 3 into the hotfix.
- **Q2:** ship the CSRF/`Host` fix first, as a hotfix PR before Phase 1, released as 0.2.2.
- **Q3:** (a). The standards' `image.yml` got compatible `release-tags` and `multi-arch` inputs
  and `v1` moved to them (standards `68401ea`, recorded in its `decisions.md` 2026-11-16); the
  Python template gained `release.yml`, which checks the tag against `pyproject.toml` and calls
  `image.yml` with both. That closes standards gaps 1 and 2. Phase 1 uses the template's
  `release.yml`; release tags stay `:X.Y.Z`, `:X.Y` and `:latest`, now amd64 and arm64 each
  built natively, and `:main` becomes amd64 only.
- **Q4–Q8, Q10–Q13:** accepted as recommended (multi-arch kept; Python 3.13; `ruff format` commit
  in `.git-blame-ignore-revs`, PR 1 merged with a merge commit; MIT `LICENSE`; releases after
  Phases 2 and 3; Dependabot monthly; test data left as is; Phases 1–5; the full Phase 5 pass).
- **Q9:** nothing polls `/healthz`. Remove it once `/api/health` exists (Phase 2).

## Questions for John

1. **Sign-in for the web UI.** Options: (a) none in the app, recorded as an exception, relying on
   the network and/or the reverse proxy's auth, with Phase 3's CSRF and `Host` checks; (b) an
   optional built-in password (`UI_PASSWORD`, one argon2-hashed secret, a session cookie) that
   other users can turn on; (c) full sign-in per the standards (own sessions plus OIDC). Recommend
   **(a) plus the CSRF and `Host` checks**, and the compose example binding the port for a proxy
   (`127.0.0.1:8080:8080` or the proxy's network) with the README saying why. It's single-user,
   holds no credential the UI can show, and its worst writes (a tag, a maintenance entry, a
   dismissed match) are visible and reversible; the cross-site path is what reaches beyond the
   LAN, and the checks close it. Choose (b) if you expect people to run it on a shared network.
2. **How is your instance reached today,** and should Phase 3 wait? (LAN port only, or behind the
   proxy, with or without its auth.) Recommend: if it's behind a proxy without auth or reachable
   from outside the LAN, ship the CSRF/`Host` fix first as a small PR before Phase 1 (merged by
   hand, released); otherwise keep it in Phase 3.
3. **Publishing releases (mode B).** (a) Add compatible inputs to the standards' `image.yml` (a
   `tags` input, release pushes, multi-arch) and move `v1`, before PR 1; or (b) keep this repo's
   own publish job in a local called workflow, brought to standard (`ubuntu-24.04`, current action
   majors, `GIT_REVISION` build arg), with the template's `image` job building on PRs only.
   Recommend **(a)**: the profile already describes mode B, and the next released tool would need
   the same; (b) if you'd rather not wait on a standards change.
4. **Multi-arch:** keep publishing amd64 and arm64? Recommend **yes**: your host is arm64 and most
   other users are on amd64. Under (a) above, arm64 natively on `ubuntu-24.04-arm` if the merge job
   is built, otherwise QEMU, which is fast enough for a pure-Python image.
5. **Python version:** 3.13 (the profile's stated "current stable minus one") or 3.14 (what that
   rule gives now that 3.15 is out)? Recommend **3.13**, matching the profile text, until the
   standards settle the rule (open question there).
6. **`ruff format`:** one commit in the Phase 1 PR, named in `.git-blame-ignore-revs`, with PR 1
   merged with a merge commit so that SHA survives? Recommend **yes** (17 files, formatting only).
7. **LICENSE:** add the MIT `LICENSE` file `pyproject.toml` already declares? Recommend **yes**:
   without the file GitHub shows no licence and the declared one is easy to miss.
8. **Release cadence for the sweep:** release after Phase 2 (0.3.0, watched) and after Phase 3
   (0.3.1)? Recommend **yes**; Phases 4 and 5 ride in whatever release comes next.
9. **`/healthz`:** does an uptime monitor or anything outside the repo poll it? Recommend: if not,
   remove it once `/api/health` exists; if so, keep it and say so in `docs/DEPLOYMENT.md`.
10. **Dependabot cadence:** monthly and grouped (as private repos) or weekly (allowed for public
    repos)? Recommend **monthly**: a merge here deploys nothing, but each Dependabot PR is a hand
    merge.
11. **Test data from your own inventory** (`tests/test_matching.py:84–117`: tool and TV-stick
    names with purchase dates): leave as is, or make them generic in Phase 3? Recommend **leave
    them**: no serials or locations, and they're the regression cases for real false positives.
    History stays as it is either way.
12. **Scope:** Phases 1–5 in the five PRs above, Phase 6 out except uv and Python 3.13? Recommend
    **yes**.
13. **Phase 5:** do the small design pass (contrast fixes, standard token names, theme toggle,
    copy and casing), or only the contrast fixes inside Phase 3? Recommend **the full small pass**:
    it's one stylesheet and three templates, and two of the failing pairs are on the main buttons
    and badges.
