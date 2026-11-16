# homebox-recall-checker

Checks the items in your [Homebox](https://homebox.software) inventory against
[CPSC](https://www.cpsc.gov/Recalls) consumer product recalls and
[NHTSA](https://www.nhtsa.gov/recalls) recalls for car seats and motor-vehicle equipment.
When it finds a match it can:

- notify you through [Apprise](https://github.com/caronc/apprise), which covers ntfy, Pushover, Discord, email, Gotify, Home Assistant and 100+ others;
- tag the item **Recall** in Homebox and add a maintenance entry with the hazard, remedy and link;
- list every match in a small web UI where you confirm or dismiss it.

Requires **Homebox 0.26+**, which has the `/v1/entities` API.

## How it works

1. **Recalls**, stored in SQLite under `/data`:
   - **CPSC**: the first run mirrors the whole database, about 10k recalls (28 MB, roughly 10 s). Later runs only fetch recalls published or updated since the last sync.
   - **NHTSA**: NHTSA's JSON API only looks up vehicles by make/model/year, so the checker reads its daily [flat file](https://static.nhtsa.gov/odi/ffdd/rcl/RCL.txt) instead (15 MB zipped, post-2010). That file covers child seats and aftermarket equipment such as inverters, RV fridges, jacks and chargers. It lists models and part numbers explicitly, plus the dates affected units were made. Vehicles and tires are off by default (`NHTSA_TYPES`).
2. **Items**: pages through Homebox entities. Locations, archived items and sold items are skipped. Full item details are fetched only when an item's `updatedAt` changes.
3. **Matching**: CPSC almost never fills in its structured model field, so its model numbers are pulled from the recall's free text. NHTSA's listed models and part numbers are matched directly. Specs such as `40V`, `4.0Ah` or `1200W` never count as model numbers. If an item has a Model Number, it's trusted over anything in the item's name. Each match gets a confidence tier:

   | Tier | Signal |
   |---|---|
   | **high** | UPC/EAN match; *or* the item's model number (or an NHTSA-listed model/part number) appears in the recall **and** the brand matches |
   | **medium** | model number appears but the brand doesn't match; or a model variant/prefix plus brand; or a product line (e.g. IKEA "MALM", Graco "SnugRide Turn & Slide") plus brand; or brand plus *every* distinctive word of the item name, when the recall doesn't list models that rule yours out |
   | **low** | brand plus some overlapping product words. Shown in the UI only by default. |

   Two purchase-date checks cap a match at **low**: buying more than `PURCHASE_GRACE_DAYS` after the recall, and buying before the first affected unit was made.

4. **Notify**: new matches at or above `MIN_NOTIFY_TIER` are sent once, batched into a single message. Dismissed matches never re-notify. If delivery fails, it's retried on the next run.
5. **Write-back**: matches at or above `AUTO_WRITEBACK_TIER`, plus anything you **Confirm** in the UI, get the tag and a maintenance entry. Write-back is idempotent.

**Better matches:** fill in **Manufacturer** and **Model Number** in Homebox. A custom field named `UPC`, `EAN` or `Barcode` enables exact UPC matching.

## Deploy (alongside Homebox)

1. In Homebox, go to **Profile → API Keys** and create a key. It starts with `hb_`.
2. Add the service from [`compose.example.yml`](compose.example.yml) to your Homebox stack.
3. Put it behind your reverse proxy (with the proxy's auth), set `PUBLIC_URL` to the address you
   open it at, and open the UI. The first check runs at startup. See
   [Access and security](#access-and-security) before exposing the port any other way.

### Images, releases and updates

GitHub Actions builds the images and pushes them to GHCR:

| Event | Tags | Platforms |
|---|---|---|
| Merge to `main` | `:main`, `:sha-<commit>` | amd64 |
| Published GitHub release `vX.Y.Z` | `:X.Y.Z`, `:X.Y`, `:latest`, `:sha-<commit>` (a pre-release gets only its full version) | amd64 and arm64 |

The compose example uses `:latest`, which only moves when you publish a release. Turn on Dockhand's
auto-update (or use **Check for updates**) for the container, and each release rolls out on its
own schedule. To cut a release:

```sh
# merge a PR that bumps `version` in pyproject.toml; then, on that commit of main:
gh release create v0.2.0 --generate-notes
```

CI fails the release build if the tag doesn't match the `pyproject.toml` version. The first push
creates the GHCR package as **private**, so either make it public or give the deploy host registry
credentials.

## Access and security

**The web UI has no sign-in, by design.** Anyone who can reach it can see matched items (serial
numbers, locations, purchase dates), confirm a match (which writes a tag and a maintenance entry to
Homebox), dismiss one (which silences that alert for good), start a check and send a test
notification. Run it only:

- behind a reverse proxy that requires sign-in (forward auth, basic auth, an SSO gateway), or
- on a network where everyone who can reach it may do those things.

The compose example binds the port to `127.0.0.1`, for a proxy on the same host. Never publish it
to the internet without the proxy's auth in front.

Two checks stop *other web pages* from using your browser against the UI:

- **Cross-site writes are refused.** Every request that changes something (confirm, dismiss,
  reopen, run a check, test notification) must come from the UI's own pages: a browser request
  whose `Sec-Fetch-Site` isn't `same-origin`, or whose `Origin` isn't this UI, gets a 403.
  `Origin` is compared with the `Host` header the request arrived with, or with `PUBLIC_URL`.
  Clients that send neither header (curl, scripts) are allowed, so the network or proxy is still
  what decides who may use it.
- **Unknown host names are refused** (400), which stops DNS rebinding: a page that points its own
  name at the UI's address still sends its own name. `ALLOWED_HOSTS` lists the names the UI
  answers to. Unset, it allows the host name in `PUBLIC_URL`, any IP address (rebinding needs a
  name, so an address is safe) and `localhost`. Set it, and only those names (plus `localhost`
  and `127.0.0.1`, for the container healthcheck) are allowed; `*` turns the check off.

**Upgrading from 0.2.1 or earlier:** if you open the UI by a host name (through a proxy, or a LAN
name such as `myserver.lan:8080`), set `PUBLIC_URL` to that address or add the name to
`ALLOWED_HOSTS`, or the UI answers "Unknown host" (400). Opening it by IP address keeps working.
If your proxy rewrites the `Host` header, set `PUBLIC_URL` to the address your browser uses, or
confirm/dismiss will answer 403.

## Configuration

| Variable | Default | |
|---|---|---|
| `HOMEBOX_URL` | `http://homebox:7745` | API base, as reached from the container |
| `HOMEBOX_TOKEN` | *(required)* | Homebox API key |
| `HOMEBOX_PUBLIC_URL` | `HOMEBOX_URL` | Browser-facing Homebox URL, used for links |
| `PUBLIC_URL` | | Browser-facing URL of this UI, used for "Review match" links; its host is allowed and its origin may send writes |
| `ALLOWED_HOSTS` | `PUBLIC_URL`'s host, any IP, `localhost` | Comma-separated host names the UI answers to; `*` turns the check off ([Access and security](#access-and-security)) |
| `APPRISE_URLS` | | Space- or comma-separated Apprise URLs |
| `APPRISE_CONFIG` | | Path to an Apprise config file (YAML/text) |
| `CHECK_INTERVAL_HOURS` | `24` | |
| `CHECK_CRON` | | Crontab expression; overrides the interval (set `TZ`) |
| `RUN_ON_STARTUP` | `true` | |
| `MIN_NOTIFY_TIER` | `medium` | `low`, `medium` or `high` |
| `AUTO_WRITEBACK_TIER` | `high` | |
| `WRITEBACK` | `true` | Set `false` to never modify Homebox |
| `RECALL_TAG_NAME` | `Recall` | Created if missing |
| `PURCHASE_GRACE_DAYS` | `365` | |
| `RECALL_SOURCES` | `cpsc,nhtsa` | Which feeds to check |
| `NHTSA_TYPES` | `C,E` | `C` child seats, `E` equipment, `T` tires, `V` vehicles |
| `CPSC_BASE_URL` | CPSC's recall API | Override only for testing |
| `NHTSA_FLAT_FILE_URL` | NHTSA's recall flat file | Override only for testing |
| `DATA_DIR` | `/data` | SQLite location |
| `UI_HOST` | `0.0.0.0` | Address the UI listens on inside the container |
| `UI_PORT` | `8080` | |
| `LOG_LEVEL` | `INFO` | |

## CLI

```sh
recall-checker serve              # scheduler + web UI (container default)
recall-checker check --dry-run    # one cycle; records matches, sends/writes nothing
recall-checker check              # one full cycle
recall-checker test-notify        # send a test message through Apprise
```

The web UI also has a **Test notification** button. It shows whether delivery worked and, if it
failed, Apprise's reason (connection refused, auth error, and so on). From the host:
`docker exec <container> recall-checker test-notify`.

## Development

Needs [uv](https://docs.astral.sh/uv/); it installs Python 3.13 (`.python-version`) if missing.

```sh
uv sync                           # .venv from uv.lock, dev tools included
cp .env.example .env              # point at a Homebox instance
uv run ruff check && uv run ruff format --check && uv run pytest
uv run recall-checker check --dry-run
```

Dependencies are locked in `uv.lock`, which CI and the image install exactly
(`uv sync --locked`). After changing them in `pyproject.toml`, run `uv lock` and commit both.

Recall sources (`sources/`) and the notifier (`notify/`) are small protocols, so new sources (FDA, Health Canada) or native notifiers can be added without touching the matcher. A source that changes how it normalizes records bumps its `parser_version`, which makes the next run re-fetch and re-parse that source in full.
