# Decisions

Project decisions, newest last. Each entry: date, the decision, why, and what was rejected.
Cross-project standards live in https://github.com/ki4hrg/standards and are not repeated here;
the entries below are this repo's exceptions to them and choices they leave open. The audit
and John's answers behind them are in `docs/plans/standards-conformance.md`.

## 2026-11-16 — Stays a public repo on the personal account

The standards move app repos into John's GitHub organization so they can share its
self-hosted runner. This repo is public, so it can't use that runner (the runner group is
closed to public repos), and its Actions minutes are free. It stays at
`ki4hrg/homebox-recall-checker`, which also keeps the published image path
`ghcr.io/ki4hrg/homebox-recall-checker` that existing installs pull. `PR_RUNNER`,
`MAIN_RUNNER` and `IMAGE_RUNNER` stay unset. Rejected: moving it into the organization.

## 2026-11-16 — Deploy mode B: versioned releases, published by the shared `image.yml`

The service runs inside a Homebox stack this repo doesn't own, and other Homebox users
install it, so there is no Dockhand webhook and no `deploy` job. It is versioned like a
released tool: `version` in `pyproject.toml`, `vX.Y.Z` tags, and
`gh release create vX.Y.Z --generate-notes`. Publishing a release runs `release.yml`, which
checks the tag against `pyproject.toml` and calls the standards' `image.yml` with
`release-tags` and `multi-arch`: `:X.Y.Z`, `:X.Y` and `:latest` (a pre-release gets only its
full version), the same tags the repo published before. Hosts pull `:latest` through their
auto-update (Dockhand's, on John's host). `ci.yml` pushes `:main` and `:sha-<sha>` on every
merge; nothing installed pulls them by default.

The running revision is checked by hand after a release (the image's
`org.opencontainers.image.revision` label, and `/api/health` once Phase 2 adds it).

Rejected: a repo-local publish workflow (John chose to extend `image.yml` so every released
tool publishes the same way, standards `decisions.md`); a webhook deploy.

## 2026-11-16 — Multi-arch release images; `:main` is amd64 only

Release images are built for amd64 and arm64, each on a native GitHub runner, and pushed as
one manifest list: John's host is arm64 and most other users are on amd64. The previous
workflow built both under QEMU on every push to `main` as well; `ci.yml` now builds `:main`
and PR images for amd64 only, as the template does, so an arm64 host that pins `:main` must
pin a release instead. Rejected: QEMU builds; multi-arch on every merge (nothing deployed
pulls `:main`).

## 2026-11-16 — `compose.example.yml` is a snippet for users, not a deployed stack

It is pasted into someone's own Homebox stack, so the `compose.yaml` name and the git-stack
rules (webhook, Force redeploy) don't apply. It still follows the image rules: no `build:`,
`pull_policy: always` on the mutable tag. Rejected: renaming it to `compose.yaml`, which
would read as a stack to deploy.

## 2026-11-16 — No sign-in in the web UI

`security.md` asks every entry point to authenticate. This UI is single-user and relies on
the network, or the reverse proxy's auth, to decide who reaches it. It holds no credential the
UI can show, and its writes (a `Recall` tag, a maintenance entry, a dismissed match) are
visible and reversible. What reached beyond the LAN was the cross-site path, closed in 0.2.2:
writes are refused unless same-origin, the UI answers only to allowed `Host` names (against DNS
rebinding), and the compose example binds the port to `127.0.0.1` for a proxy. The README's
"Access and security" section says so to users. Rejected: an optional built-in password, and
full sign-in per the standards (John, Q1).

## 2026-11-16 — Python 3.13

`requires-python = ">=3.13"`, `.python-version` and the `python:3.13-slim` base image, as the
Python profile states ("current stable minus one, 3.13 today"). Dependabot ignores Python
image minors and majors; moving to 3.14 is a decision of its own. Rejected: 3.14, until the
standards settle how the rule reads now that 3.15 is out (John, Q5).

## 2026-11-16 — Dependabot monthly, though the repo is public

A public repo may update weekly (its minutes are free), but each Dependabot PR is merged by
hand, and a merge here deploys nothing until a release. Security updates still open as soon as
an advisory lands. `apprise`, `fastapi`, `httpx` and `apscheduler` are upper-bounded in
`pyproject.toml` and their majors ignored, so a major is planned work: Apprise 2 changed its
API, APScheduler 4 is a rewrite, and FastAPI and httpx are still 0.x, where 1.0 is the break
to plan for. Rejected: weekly (John, Q10).

## 2026-11-16 — Formatting commit

`ruff format` was applied in one formatting-only commit, listed in `.git-blame-ignore-revs`.
The Phase 1 PR that holds it is merged with a merge commit, not squashed, so that commit's SHA
reaches `main` unchanged (John, Q6). Every later PR is squash-merged as usual.

## 2026-11-16 — `/api/health` adds `version`; `/healthz` stays one release as a deprecated alias

`GET /api/health` answers `{ ok, revision, version }` with `Cache-Control: no-store`. `revision`
is the standard's (`GIT_REVISION`, `null` when the image was built without it). `version` is
added because people who install a released tool know it by version, not by commit; it is read
once from the package metadata, so the route stays as cheap as the standard asks. The running
flag and last-run summary `/healthz` carried are left out: the last run is a SQLite read, and the
UI already shows both.

`/healthz` keeps its old answer, with `Deprecation: true` and a `Link` to `/api/health`, from
0.3.0 until a later release removes it. Nothing in this repo polls it (John, Q9), but a compose
file written for 0.2.x could override the image's healthcheck with it, and other people's installs
are out of sight. The image's own healthcheck moved to `/api/health`. Rejected: removing it in
0.3.0 (the plan's first answer to Q9); a redirect (a healthcheck client may not follow it).

## 2026-11-16 — Settings refuse a bad configuration at boot

Every setting is checked in `Settings` (pydantic-settings) before anything starts: the Homebox
token required and non-blank, URLs `http(s)` with a host, `CHECK_CRON` parsed by APScheduler,
`RECALL_SOURCES` and `NHTSA_TYPES` against the known names, tiers, log level and number ranges.
A bad value exits 1 with one `Error: <VARIABLE>: <problem>` line each and no traceback, and no
line prints a value: a URL can carry credentials. The token and `APPRISE_URLS` are left out of
the settings' `repr`. `restart: unless-stopped` still restarts a refused container; the compose
guard on `HOMEBOX_RECALL_TOKEN` stops the commonest case before it starts. Rejected: `SecretStr`
for the token (every call site changes for what `repr=False` already gives).
