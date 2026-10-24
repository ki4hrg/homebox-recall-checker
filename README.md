# homebox-recall-checker

Checks the items in your [Homebox](https://homebox.software) inventory against
[CPSC](https://www.cpsc.gov/Recalls) consumer product recalls. When it finds a match it can:

- notify you through [Apprise](https://github.com/caronc/apprise), which covers ntfy, Pushover, Discord, email, Gotify, Home Assistant and 100+ others;
- tag the item **Recall** in Homebox and add a maintenance entry with the hazard, remedy and link;
- list every match in a small web UI where you confirm or dismiss it.

Requires **Homebox 0.26+**, which has the `/v1/entities` API.

## How it works

1. **Recalls**: the first run mirrors the whole CPSC recall database, about 10k recalls (28 MB, roughly 10 s). Later runs only fetch recalls published or updated since the last sync. The data is stored in SQLite under `/data`.
2. **Items**: pages through Homebox entities. Locations, archived items and sold items are skipped. Full item details are fetched only when an item's `updatedAt` changes.
3. **Matching**: CPSC almost never fills in its structured model field, so model numbers are pulled from the recall's free text. Each match gets a confidence tier:

   | Tier | Signal |
   |---|---|
   | **high** | UPC/EAN match, *or* the item's model number appears in the recall **and** the brand matches |
   | **medium** | model number appears but the brand doesn't match; or a model variant/prefix plus brand; or a product line (e.g. IKEA "MALM") plus brand; or brand plus *every* distinctive word of the item name |
   | **low** | brand plus some overlapping product words. Shown in the UI only by default. |

   An item purchased more than `PURCHASE_GRACE_DAYS` after the recall drops one tier.

4. **Notify**: new matches at or above `MIN_NOTIFY_TIER` are sent once, batched into a single message. Dismissed matches never re-notify. If delivery fails, it's retried on the next run.
5. **Write-back**: matches at or above `AUTO_WRITEBACK_TIER`, plus anything you **Confirm** in the UI, get the tag and a maintenance entry. Write-back is idempotent.

**Better matches:** fill in **Manufacturer** and **Model Number** in Homebox. A custom field named `UPC`, `EAN` or `Barcode` enables exact UPC matching.

## Deploy (alongside Homebox)

1. In Homebox, go to **Profile → API Keys** and create a key. It starts with `hb_`.
2. Add the service from [`compose.example.yml`](compose.example.yml) to your Homebox stack.
3. Open the UI on port 8080. The first check runs at startup.

The image is built and pushed to GHCR by GitHub Actions. The first push creates the package as **private**: either make it public, or give the deploy host registry credentials. The CI `deploy` job pings a Dockhand webhook when `DOCKHAND_WEBHOOK_URL` (and optionally `DOCKHAND_WEBHOOK_TOKEN`) is set under Actions secrets or variables.

> The web UI has no authentication. Keep it on your LAN or behind your reverse proxy's auth.

## Configuration

| Variable | Default | |
|---|---|---|
| `HOMEBOX_URL` | `http://homebox:7745` | API base, as reached from the container |
| `HOMEBOX_TOKEN` | *(required)* | Homebox API key |
| `HOMEBOX_PUBLIC_URL` | `HOMEBOX_URL` | Browser-facing Homebox URL, used for links |
| `PUBLIC_URL` | | Browser-facing URL of this UI, used for "Review match" links |
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
| `DATA_DIR` | `/data` | SQLite location |
| `UI_PORT` | `8080` | |
| `LOG_LEVEL` | `INFO` | |

## CLI

```sh
recall-checker serve              # scheduler + web UI (container default)
recall-checker check --dry-run    # one cycle; records matches, sends/writes nothing
recall-checker check              # one full cycle
recall-checker test-notify        # send a test message through Apprise
```

## Development

```sh
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env              # point at a Homebox instance
.venv/bin/pytest && .venv/bin/ruff check src tests
.venv/bin/recall-checker check --dry-run
```

The recall source (`sources/`) and the notifier (`notify/`) are small protocols, so other sources (NHTSA, FDA, Health Canada) or native notifiers can be added without touching the matcher.
