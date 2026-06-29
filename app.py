"""AI Briefing web UI.

Serves a single-page dark-themed UI at http://localhost:5000 for configuring
topics, YouTube channels, and delivery settings, and lets you trigger a test
briefing on demand.
"""
import os
import threading

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


if __name__ == "__main__":
    # Defaults to port 5000 (http://localhost:5000). On macOS, port 5000 is often
    # taken by the AirPlay Receiver — disable it in System Settings ▸ General ▸
    # AirDrop & Handoff, or override here, e.g. `PORT=5050 python app.py`.
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
