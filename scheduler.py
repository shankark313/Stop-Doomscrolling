"""Daily scheduler for AI Briefing.

Runs briefing.py every day at the time configured in config.json
(`delivery_time`, HH:MM). The schedule is re-read every minute, so changing the
delivery time in the web UI takes effect without restarting this process.

Usage:
    python scheduler.py          # run on the configured daily schedule
    python scheduler.py --now    # generate and send one briefing immediately, then keep scheduling
"""
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

from briefing import log, run_briefing
from config_store import load_config

load_dotenv()

JOB_ID = "daily_briefing"
_current_time = {"value": None}


def _do_run():
    try:
        run_briefing(deliver=True)
    except Exception as exc:  # noqa: BLE001 - keep the scheduler alive on failures
        log(f"Briefing run failed: {exc}")


def _ensure_schedule(scheduler):
    """(Re)schedule the daily job if the configured delivery time changed."""
    delivery_time = load_config().get("delivery_time", "08:00")
    if delivery_time == _current_time["value"]:
        return
    try:
        hour, minute = (int(p) for p in delivery_time.split(":"))
    except ValueError:
        log(f"Invalid delivery_time '{delivery_time}', defaulting to 08:00.")
        hour, minute = 8, 0
        delivery_time = "08:00"

    scheduler.add_job(
        _do_run,
        trigger=CronTrigger(hour=hour, minute=minute),
        id=JOB_ID,
        replace_existing=True,
    )
    _current_time["value"] = delivery_time
    log(f"Daily briefing scheduled for {delivery_time} ({_local_tz()}).")


def _local_tz():
    import time as _time

    return _time.tzname[0]


def main():
    if "--now" in sys.argv:
        log("Running a briefing immediately (--now).")
        _do_run()

    scheduler = BlockingScheduler()
    _ensure_schedule(scheduler)

    # Re-check the configured time every minute so UI changes are picked up live.
    scheduler.add_job(
        lambda: _ensure_schedule(scheduler),
        trigger="interval",
        seconds=60,
        id="config_watch",
    )

    log("Scheduler started. Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log("Scheduler stopped.")


if __name__ == "__main__":
    main()
