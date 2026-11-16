# Deployment

## How a release reaches a host

**Mode B: versioned releases** (`docs/DECISIONS.md`). The service runs inside someone's Homebox
stack, which this repo doesn't own, so there is no deploy webhook and a merge to `main` deploys
nothing.

1. A merge to `main` runs `.github/workflows/ci.yml`: checks, the dependency audit, then an
   amd64 image pushed as `:main` and `:sha-<commit>`. Nothing installed pulls these by default.
2. A release is cut by merging a PR that bumps `version` in `pyproject.toml`, then
   `gh release create vX.Y.Z --generate-notes` on that commit of `main`.
3. Publishing the release runs `.github/workflows/release.yml`: it fails unless the tag matches
   `pyproject.toml`, then builds amd64 and arm64 natively and pushes one manifest list as
   `:X.Y.Z`, `:X.Y` and `:latest`. Every image carries the commit in its
   `org.opencontainers.image.revision` label and in `GIT_REVISION`.
4. A host picks the release up in one of two ways:
   - **Auto-update** (the compose example's default): the service pulls `:latest` with
     `pull_policy: always`, and the host's updater (Dockhand's auto-update or "Check for
     updates", Diun, or a manual `docker compose pull && docker compose up -d`) recreates it.
     The maintainer's host runs Dockhand's auto-update, on arm64.
   - **Pinned:** `RECALL_CHECKER_IMAGE=ghcr.io/ki4hrg/homebox-recall-checker:X.Y.Z` (or `:X.Y`
     for patch releases only), bumped by hand for each release.

The GHCR package is public, so hosts pull without credentials.

## Checking what's running

The tag name proves nothing; check the revision. After a release rolls out, from the host:

```sh
# The commit and version the running process reports (the image healthcheck uses this route).
docker exec <container> python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/api/health').read().decode())"

# The commit the image was built from.
docker inspect -f '{{ index .Config.Labels "org.opencontainers.image.revision" }}' <container>

# Healthy, not root (uid 10001), and the data volume writable.
docker inspect -f '{{ .State.Health.Status }}' <container>
docker exec <container> id
docker exec <container> touch /data/.write-test && docker exec <container> rm /data/.write-test
```

Both revisions should equal the commit the release tag points at (`git rev-list -n1 vX.Y.Z`).
From a browser on the LAN, `/api/health` answers the same JSON. The process also prints
`recall-checker X.Y.Z, revision <commit>` as the first line of its log, before it reads its
settings, so a container that refuses its config still says what it is running.

There is no automatic check after a release: the service is LAN-only, so GitHub can't reach
`/api/health`, and the release is checked by hand as above.

## Healthcheck

The image's `HEALTHCHECK` calls `http://127.0.0.1:8080/api/health` every 60 s (30 s start
period, 3 retries). The route touches no database, Homebox or recall feed, so a long check run
or a Homebox outage never marks the container unhealthy; Homebox and feed failures show in the
log and the UI's last-run summary instead. `127.0.0.1` and `localhost` always pass the
`ALLOWED_HOSTS` check, whatever it is set to. The compose example sets no healthcheck of its own.

**`/healthz` is deprecated as of 0.3.0.** It still answers with its old body (`ok`, `running`,
`last_run`, read from SQLite) and a `Deprecation: true` header, for a compose file or monitor
that still calls it, and it will be removed in a later release. Point anything that uses it at
`/api/health`.

## Required settings

The full list is the README's Configuration table; `.env.example` shows each one.

| Variable | Required | Guard |
|---|---|---|
| `HOMEBOX_TOKEN` | **yes** | The compose example passes `${HOMEBOX_RECALL_TOKEN:?…}`, so an unset or empty token stops `compose up` with that message. The app refuses a blank one too |
| `HOMEBOX_URL` | no (`http://homebox:7745`) | An `http(s)` URL with a host |
| `PUBLIC_URL` | no, but set it behind a proxy | An `http(s)` URL; its host is allowed and its origin may send writes |
| `APPRISE_URLS` | no | Optional: without it, nothing is notified. Never logged (only each URL's scheme) |

The app checks every setting at startup (`Settings` in `src/recall_checker/config.py`). A bad one
exits 1 with one `Error: <VARIABLE>: <problem>` line per problem, never printing a value:

- `HOMEBOX_TOKEN` missing or blank;
- `HOMEBOX_URL`, `HOMEBOX_PUBLIC_URL`, `PUBLIC_URL`, `CPSC_BASE_URL`, `NHTSA_FLAT_FILE_URL` not
  an `http(s)://` URL with a host (the optional ones may be empty);
- `CHECK_CRON` not a five-field crontab line; `CHECK_INTERVAL_HOURS` not above 0;
- `RECALL_SOURCES` empty or naming something other than `cpsc`, `nhtsa`;
- `NHTSA_TYPES` naming something other than `C`, `E`, `T`, `V`, or empty while `nhtsa` is on;
- `MIN_NOTIFY_TIER` or `AUTO_WRITEBACK_TIER` not `low`, `medium` or `high`;
- `LOG_LEVEL` not a level name; `UI_PORT` outside 1–65535; `PURCHASE_GRACE_DAYS` negative;
- `ALLOWED_HOSTS` entries that are URLs rather than host names.

With `restart: unless-stopped`, a container that refuses its config restarts and prints the
same lines again until the variable is fixed; `docker logs <container>` shows them.

## Host and exposure

- Platforms: `linux/amd64` and `linux/arm64` for releases; `:main` is amd64 only.
- Runs as the image's non-root `app` user (uid 10001). It writes only `/data`, a named volume the
  image creates and hands to that user, so a fresh volume needs no `chown`.
- The UI has no sign-in (`docs/DECISIONS.md`, README "Access and security"). The compose example
  publishes `127.0.0.1:8080:8080`, for a reverse proxy on the same host with its own auth; a
  proxy on the same Docker network needs no published port.
- Outbound: Homebox over the stack network, CPSC (`saferproducts.gov`) and NHTSA
  (`static.nhtsa.gov`) over HTTPS, and whatever the Apprise URLs name. Feed downloads are capped
  (`docs/ARCHITECTURE.md`): the NHTSA zip passes through a temporary file of up to 128 MB in the
  container's `/tmp`, and a check peaks at about 190 MB of memory.

## Backups

SQLite at `/data/recall-checker.db`. The recalls and item snapshots are rebuilt from the feeds
and Homebox on the next run; match statuses (confirmed, dismissed) and the notification history
are not. Back up the `/data` volume with the rest of the Homebox stack (on the maintainer's host,
its nightly volume snapshot). Before an upgrade you're unsure of, take a copy by hand:
`docker exec <container> python -c "import sqlite3; sqlite3.connect('/data/recall-checker.db').backup(sqlite3.connect('/data/backup.db'))"`.

The schema is created with `CREATE TABLE IF NOT EXISTS` on start; there are no migrations to roll
back.

## Rollback

Pin the previous release: set `RECALL_CHECKER_IMAGE=ghcr.io/ki4hrg/homebox-recall-checker:<previous X.Y.Z>`
in the stack's environment and recreate the service, then check the revision as above. An
auto-updater won't move a pinned version tag, so the pin holds until you remove it. Once a fixed
release is out, go back to `:latest`.

Each image carries its own healthcheck, so a release from before `/api/health` (0.2.x) still
checks itself on `/healthz`. A compose file that overrides the healthcheck must match the image
it runs: `/api/health` fails against 0.2.x.

Fix forward otherwise: a `revert:` PR on `main`, then a patch release.
