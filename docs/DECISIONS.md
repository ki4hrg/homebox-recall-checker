# Decisions

Project decisions, newest last. Each entry: date, the decision, why, and what was rejected.
Cross-project standards live in https://github.com/ki4hrg/standards and are not repeated here;
the entries below are this repo's exceptions to them and choices they leave open. The audit
and John's answers behind them are in `docs/plans/standards-conformance.md`.

## Index

Every decision below is in force.

- [Stays a public repo on the personal account](#2026-11-16--stays-a-public-repo-on-the-personal-account)
- [Deploy mode B: versioned releases, published by the shared `image.yml`](#2026-11-16--deploy-mode-b-versioned-releases-published-by-the-shared-imageyml)
- [Multi-arch release images; `:main` is amd64 only](#2026-11-16--multi-arch-release-images-main-is-amd64-only)
- [`compose.example.yml` is a snippet for users, not a deployed stack](#2026-11-16--composeexampleyml-is-a-snippet-for-users-not-a-deployed-stack)
- [No sign-in in the web UI](#2026-11-16--no-sign-in-in-the-web-ui)
- [Python 3.13](#2026-11-16--python-313)
- [Dependabot monthly, though the repo is public](#2026-11-16--dependabot-monthly-though-the-repo-is-public)
- [Formatting commit](#2026-11-16--formatting-commit): one `ruff format` commit, ignored by `git blame`
- [`/api/health` adds `version`; `/healthz` stays one release as a deprecated alias](#2026-11-16--apihealth-adds-version-healthz-stays-one-release-as-a-deprecated-alias)
- [Settings refuse a bad configuration at boot](#2026-11-16--settings-refuse-a-bad-configuration-at-boot)
- [UI input refused with a 400; one error page; notices are codes](#2026-11-16--ui-input-refused-with-a-400-one-error-page-notices-are-codes)
- [Security headers, with no inline style or script](#2026-11-16--security-headers-with-no-inline-style-or-script)
- [Feed downloads and request bodies are capped](#2026-11-16--feed-downloads-and-request-bodies-are-capped)
- [Standard tokens as `--color-*` custom properties; tiers on the status colours](#2026-11-16--standard-tokens-as---color--custom-properties-tiers-on-the-status-colours)
- [A theme script from this origin: `script-src 'self'`](#2026-11-16--a-theme-script-from-this-origin-script-src-self)
- [Dismiss is not destructive, so it doesn't confirm](#2026-11-16--dismiss-is-not-destructive-so-it-doesnt-confirm)

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

## 2026-11-16 — UI input refused with a 400; one error page; notices are codes

Every query, path and form value the UI takes is checked against the values it can be: `status`,
`tier`, `notice`, the match id (a positive 64-bit integer, so SQLite never sees a larger one) and
the form's `status`. Anything else answers 400 with a short message, where `?tier=bogus` was a
500 before. Unknown values are refused rather than ignored, so a typo in a bookmark says so
instead of showing a different list.

The exception handlers in `create_app` are the one place errors are shown: `AppError` with its
fixed message, parameter failures naming the parameter (never its value), routing errors with
fixed text, and anything else logged and shown as a generic 500. Pages get an HTML page,
`/api/*` gets `{ "error": … }`. A failed write-back to Homebox was a 502 JSON page carrying part
of Homebox's answer; it now keeps the status change, logs the detail and redirects to the match
with a fixed notice, since the next check retries it.

`?notice=` took any text and showed it (escaped) on the index page, so any link could put its own
words there, and the test-notification result travelled in the URL and the browser's history. A
notice is now a code mapped to fixed text (`NOTICES`); the test result is held in the process's
memory and `?notice=test` shows the last one. It is lost on restart, which only means no notice.
Rejected: a cookie flash (the app has no cookies and no reason to start), and rendering the
result straight from the POST (a reload would send another test).

## 2026-11-16 — Security headers, with no inline style or script

Every answer carries a CSP (`default-src 'none'; style-src 'self'; img-src 'self' https: http:;
form-action 'self'; frame-ancestors 'none'; base-uri 'none'`), `X-Frame-Options: DENY`,
`X-Content-Type-Options: nosniff` and `Referrer-Policy: same-origin`. The UI ran no script, so
none was allowed (until the theme script below added `script-src 'self'`); the five inline `style=` attributes became classes in `style.css`, and a test
keeps inline style and script out of the templates. `frame-ancestors` stops a page framing the UI
to steer a click onto Confirm or Dismiss. Images may come from any http(s) host because recall
photos are on the agencies' hosts. Rejected: `'unsafe-inline'` for styles (five attributes were
cheaper to move).

## 2026-11-16 — Feed downloads and request bodies are capped

It runs on small home servers (1 GB of memory is common). CPSC's answer is held in memory to parse
(28 MB for the full set in November 2026, several times that once parsed) and is capped at
64 MB. The NHTSA zip (15 MB) goes to
a temporary file, capped at 128 MB; its text (314 MB) is read a row at a time, refused when its
declared size is over 1 GB (zipfile never reads past it), and rows over 64 KB (the longest real
one is 3.3 KB) are skipped. Over a cap, that source's sync fails for the run, as a download
error would. The caps are constants, not settings: nobody needs to tune them, and an extra
variable is one more thing to misconfigure. Request bodies over 16 KB are refused (the only
bodies are one-field forms). Links from the feeds become links only when they are http(s) URLs.

## 2026-11-16 — Standard tokens as `--color-*` custom properties; tiers on the status colours

`style.css` defines the standards' token names (`design.md`, Tokens) as custom properties, spelled
the way Tailwind 4 spells them (`--color-page`, `--color-ink-3`, `--radius-card`), so the names
read the same as in apps that use Tailwind; whether an app without Tailwind may use plain
`--page` is still open in the standards. Type and spacing take Tailwind 4's names too (`--font-sans`, `--text-sm`, `--spacing`).
The old names map by meaning: `bg`→`page`, `fg`→`ink`, `muted`→`ink-3`, `border`→`line`,
`ok`→`ok`. The match tiers are not statuses, but they read as urgency, so they use the status
colours rather than app tokens: `high` is a solid `danger` badge, `medium` a solid `warn` badge,
`low` a neutral one (`ink-2` on `sunk`). No app colour token is added.

Every value was chosen to pass WCAG AA in both themes: text 4.5:1 on each surface it sits on,
control edges (borders, focus ring) 3:1. `tests/test_design.py` checks the pairs, that both
themes set every token and the two dark blocks agree, and that no rule uses a raw colour or
opacity. In 0.3.0 six pairs failed in dark mode or on the badges (the primary button 2.49, the
`medium` badge 2.69, the `low` badge 4.24, Dismiss 2.93, the success notice 3.56, the error
notice and error count 3.25). Rejected: keeping the white-on-colour badges and darkening the dark
theme's accent, which makes links on the dark page harder to read.

## 2026-11-16 — A theme script from this origin: `script-src 'self'`

The standards require a Light / Dark / System choice stored in `localStorage` and applied before
first paint. That needs script, which the CSP refused. `static/theme.js` is loaded by a
render-blocking `<script src>` in `<head>`, before the stylesheet; it applies the stored choice,
then shows and wires the three buttons in the footer (there is no settings page or menu). The
buttons are rendered `hidden`, so without the script the device's setting decides and no control
does nothing. The CSP adds only `script-src 'self'`: no inline script or `on…=` handler runs (the
template test refuses both), and nothing else the app answers can run as a script, because its
HTML, JSON and plain-text answers fail the `nosniff` type check. The script reads and writes only
the `theme` key, sends no request and touches no page data.

Rejected: an inline script allowed by its hash (the hash must track every edit, and the
template test could no longer refuse inline script outright); a nonce (per-request plumbing for
one fixed script); a server-side choice in a cookie (the app has no cookies, and a per-device
preference needs no request).

## 2026-11-16 — Dismiss is not destructive, so it doesn't confirm

`design.md` asks destructive actions to confirm inline. Dismiss removes nothing: the match stays
in the Dismissed list, Reopen restores it, and nothing in Homebox changes. It stays a one-click
secondary button in the danger colour. Rejected: an inline confirm, which would also need script
for something any click can undo.
