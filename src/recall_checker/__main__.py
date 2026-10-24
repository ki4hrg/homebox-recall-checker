from __future__ import annotations

import argparse
import logging
import sys

from .config import Settings, Tier, get_settings
from .db import DB
from .homebox import HomeboxClient
from .notify.apprise_ import AppriseNotifier
from .runner import Runner
from .sources.base import RecallSource
from .sources.cpsc import CPSCSource
from .sources.nhtsa import NHTSASource

log = logging.getLogger("recall_checker")


def build_sources(s: Settings) -> list[RecallSource]:
    sources: list[RecallSource] = []
    for name in s.source_names():
        if name == "cpsc":
            sources.append(CPSCSource(s.cpsc_base_url))
        elif name == "nhtsa":
            sources.append(NHTSASource(s.nhtsa_type_set(), s.nhtsa_flat_file_url))
        else:
            sys.exit(f"Unknown recall source {name!r} in RECALL_SOURCES (expected cpsc, nhtsa)")
    if not sources:
        sys.exit("RECALL_SOURCES is empty")
    return sources


def build_runner(s: Settings) -> Runner:
    if not s.homebox_token:
        sys.exit("HOMEBOX_TOKEN is required (create an API key in Homebox: Profile → API Keys)")
    return Runner(
        settings=s,
        db=DB(s.db_path),
        sources=build_sources(s),
        homebox=HomeboxClient(s.homebox_url, s.homebox_token),
        notifier=AppriseNotifier(s.apprise_url_list(), s.apprise_config),
    )


def cmd_check(s: Settings, dry_run: bool) -> int:
    runner = build_runner(s)
    res = runner.run(dry_run=dry_run)
    if res is None:
        return 1
    rows = runner.db.list_matches(None, int(Tier.low))
    note = ", dry run: nothing sent or written" if dry_run else ""
    print(f"\n{len(rows)} matches on record ({res.new_matches} new this run{note})")
    for row in rows:
        item = runner.db.get_item(row["item_id"]) or {}
        recall = runner.db.get_recall(row["recall_key"])
        name = item.get("name", "?")[:40]
        print(f"  [{Tier(row['tier']).name:<6}] {row['status']:<9} {name:<40} "
              f"<- {recall.label if recall else row['recall_key']}: "
              f"{recall.title[:70] if recall else ''}")
    return 1 if res.errors else 0


def cmd_test_notify(s: Settings) -> int:
    n = AppriseNotifier(s.apprise_url_list(), s.apprise_config)
    ok, detail = n.test(s.public_url)
    print(("OK: " if ok else "FAILED: ") + detail)
    return 0 if ok else 1


def cmd_serve(s: Settings) -> int:
    import uvicorn
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    from .web.app import create_app

    runner = build_runner(s)
    try:
        info = runner.homebox.status()
        log.info("Connected to Homebox %s", (info.get("build") or {}).get("version", "?"))
    except Exception as exc:
        log.warning("Homebox not reachable yet (%s); will retry on schedule", exc)

    sched = BackgroundScheduler(daemon=True)
    trigger = (CronTrigger.from_crontab(s.check_cron) if s.check_cron
               else IntervalTrigger(hours=s.check_interval_hours))
    job = sched.add_job(runner.run, trigger, id="check", max_instances=1, coalesce=True)
    sched.start()
    if s.run_on_startup:
        sched.add_job(runner.run, id="startup-check")
    log.info("Scheduled checks: %s", s.check_cron or f"every {s.check_interval_hours}h")
    log.debug("Next scheduled run: %s", job.next_run_time)

    uvicorn.run(create_app(runner), host=s.ui_host, port=s.ui_port,
                log_level=s.log_level.lower(), access_log=False)
    sched.shutdown(wait=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recall-checker",
                                     description="Check Homebox items against product recalls")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the scheduler and web UI (default)")
    check = sub.add_parser("check", help="run one check cycle and exit")
    check.add_argument("--dry-run", action="store_true",
                       help="match and record, but don't notify or write to Homebox")
    sub.add_parser("test-notify", help="send a test notification via Apprise")
    args = parser.parse_args(argv)

    s = get_settings()
    logging.basicConfig(level=s.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.cmd == "check":
        return cmd_check(s, args.dry_run)
    if args.cmd == "test-notify":
        return cmd_test_notify(s)
    return cmd_serve(s)


if __name__ == "__main__":
    sys.exit(main())
