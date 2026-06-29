"""AI Briefing web app.

Serves a dark-themed web UI for configuring topics, channels, Reddit communities,
RSS feeds, and delivery settings, lets you trigger a test briefing on demand, and
runs an in-process APScheduler that delivers the briefing daily at the configured
time. Designed to run as a single process (e.g. `python app.py` on Railway).
"""
import atexit
import os
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from config_store import load_config, normalize_config, save_config

load_dotenv()

app = Flask(__name__)

# Guards against overlapping manual briefing runs.
_run_lock = threading.Lock()
_running = {"active": False}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify(load_config())


@app.route("/api/config", methods=["POST"])
def update_config():
    payload = request.get_json(silent=True) or {}
    cfg = normalize_config(payload)
    save_config(cfg)
    return jsonify({"ok": True, "config": cfg})


@app.route("/api/run-now", methods=["POST"])
def run_now():
    if _running["active"]:
        return jsonify({"ok": False, "message": "A briefing is already running."}), 409

    def _worker():
        # Import lazily so the server starts without heavy deps loaded.
        from briefing import run_briefing

        try:
            run_briefing(deliver=True)
        except Exception as exc:  # noqa: BLE001 - surface in server logs
            app.logger.exception("Briefing run failed: %s", exc)
        finally:
            with _run_lock:
                _running["active"] = False

    with _run_lock:
        if _running["active"]:
            return jsonify({"ok": False, "message": "A briefing is already running."}), 409
        _running["active"] = True

    threading.Thread(target=_worker, daemon=True).start()
    return jsonify({"ok": True, "message": "Briefing started — it will arrive on Telegram shortly."})


# --------------------------------------------------------------------------- #
# In-process daily scheduler (APScheduler)
# --------------------------------------------------------------------------- #
scheduler = BackgroundScheduler()
JOB_ID = "daily_briefing"
# Timezone the daily delivery time is interpreted in (IANA name). Defaults to IST
# so "08:00" means 8am India time regardless of the server's own timezone (Railway
# runs in UTC). Override with the TIMEZONE env var, e.g. TIMEZONE="America/New_York".
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Kolkata")
_scheduler_state = {"started": False, "delivery_time": None}


def _run_briefing_job():
    """Run the daily briefing; never let an exception kill the scheduler."""
    from briefing import run_briefing

    try:
        run_briefing(deliver=True)
    except Exception as exc:  # noqa: BLE001
        app.logger.exception("Scheduled briefing failed: %s", exc)


def _ensure_schedule():
    """(Re)schedule the daily job whenever config.json's delivery_time changes."""
    delivery_time = load_config().get("delivery_time", "08:00")
    if delivery_time == _scheduler_state["delivery_time"]:
        return
    try:
        hour, minute = (int(p) for p in delivery_time.split(":"))
    except ValueError:
        app.logger.warning("Invalid delivery_time %r; defaulting to 08:00.", delivery_time)
        hour, minute = 8, 0
        delivery_time = "08:00"

    scheduler.add_job(
        _run_briefing_job,
        trigger=CronTrigger(hour=hour, minute=minute, timezone=TIMEZONE),
        id=JOB_ID,
        replace_existing=True,
    )
    _scheduler_state["delivery_time"] = delivery_time
    app.logger.info("Daily briefing scheduled for %s %s.", delivery_time, TIMEZONE)


def start_scheduler():
    """Start the background scheduler once per process."""
    if _scheduler_state["started"]:
        return
    _scheduler_state["started"] = True
    _ensure_schedule()
    # Re-check config every minute so UI changes to the delivery time apply live.
    scheduler.add_job(
        _ensure_schedule, trigger="interval", seconds=60, id="config_watch",
        replace_existing=True,
    )
    scheduler.start()
    atexit.register(lambda: scheduler.running and scheduler.shutdown(wait=False))


# Start the scheduler when the app process boots. The guard avoids a double start
# under the Werkzeug auto-reloader (debug mode): in that case only the reloaded
# child process (WERKZEUG_RUN_MAIN == "true") starts it. In production (debug off,
# or under a WSGI server like gunicorn) it starts at import time.
if (
    os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    or os.environ.get("FLASK_DEBUG", "false").lower() != "true"
):
    start_scheduler()


if __name__ == "__main__":
    # Railway (and most PaaS) provide the port via $PORT and expect 8080 by default.
    port = int(os.getenv("PORT", "8080"))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
