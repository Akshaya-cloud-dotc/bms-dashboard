"""
BMS cloud dashboard (Render).

The Raspberry Pi does all processing (Bluetooth, ML, alerts) and pushes its
latest /api/telemetry snapshot here. This app only stores the newest snapshot
and serves the dashboard page, which polls GET /api/telemetry as before.

Environment variables:
  INGEST_TOKEN        required. Shared secret the Pi sends as a Bearer token.
  DASHBOARD_USER      optional. With DASHBOARD_PASSWORD, turns on Basic Auth
  DASHBOARD_PASSWORD  optional. for the dashboard and read APIs.
  STALE_AFTER_SECONDS optional, default 15. Mark data stale if the Pi is silent.
"""
import hmac
import os
import threading
import time

from flask import Flask, Response, jsonify, request, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "")
DASH_USER = os.environ.get("DASHBOARD_USER", "")
DASH_PASS = os.environ.get("DASHBOARD_PASSWORD", "")
STALE_AFTER = float(os.environ.get("STALE_AFTER_SECONDS", "15"))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2 MB per push

_lock = threading.Lock()
_state = {"snapshot": None, "received_at": None}

# Routes that only make sense on the Pi (they read or write its local files).
PI_ONLY = (
    "/api/download_raw_log",
    "/api/download_filtered_log",
    "/api/predict_file",
    "/api/upload_datasheet",
    "/api/reset",
    "/api/debug_bms",
    "/api/live-bms-data",
)


def _same(a, b):
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _auth_required():
    return bool(DASH_USER and DASH_PASS)


@app.before_request
def _basic_auth():
    # The Pi authenticates with its own token; /health must stay open for Render.
    if request.path in ("/api/ingest", "/health") or not _auth_required():
        return None
    auth = request.authorization
    if (
        auth
        and auth.username is not None
        and auth.password is not None
        and _same(auth.username, DASH_USER)
        and _same(auth.password, DASH_PASS)
    ):
        return None
    return Response(
        "Login required.",
        401,
        {"WWW-Authenticate": 'Basic realm="BMS Dashboard"'},
    )


def _empty_snapshot():
    return {
        "buffer_length": 0,
        "status": "Waiting for the Raspberry Pi to send data",
        "prediction": None,
        "live_prediction": None,
        "active_profile_name": "Unknown",
        "live_row": None,
        "raw_row": None,
        "learning_status": None,
        "cycle_analytics": None,
        "bluetooth_connected": False,
        "is_stale": True,
        "data_age_seconds": None,
        "last_packet_time": None,
        "active_alerts": [],
        "alert_history": [],
        "historical_stats": {},
    }


@app.route("/api/ingest", methods=["POST"])
def ingest():
    if not INGEST_TOKEN:
        return jsonify({"error": "server has no INGEST_TOKEN configured"}), 503
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer ") or not _same(header[7:], INGEST_TOKEN):
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "expected a JSON object"}), 400
    with _lock:
        _state["snapshot"] = data
        _state["received_at"] = time.time()
    return jsonify({"ok": True})


@app.route("/api/telemetry", methods=["GET"])
def telemetry():
    with _lock:
        snap = _state["snapshot"]
        received = _state["received_at"]
    if snap is None:
        return jsonify(_empty_snapshot())

    out = dict(snap)
    silence = time.time() - received
    if silence > STALE_AFTER:
        # The Pi stopped pushing: do not show old numbers as live.
        out.update(
            {
                "is_stale": True,
                "bluetooth_connected": False,
                "status": "Waiting for the Raspberry Pi to send data",
                "prediction": None,
                "live_prediction": None,
                "live_row": None,
                "data_age_seconds": round((snap.get("data_age_seconds") or 0) + silence, 1),
            }
        )
    return jsonify(out)


@app.route("/api/alerts", methods=["GET"])
def alerts():
    with _lock:
        snap = _state["snapshot"] or {}
    active = snap.get("active_alerts") or []
    level = "NORMAL"
    for a in active:
        if a.get("level") == "CRITICAL":
            level = "CRITICAL"
            break
        if a.get("level") == "WARNING":
            level = "WARNING"
    return jsonify(
        {
            "active_alerts": active,
            "alert_history": snap.get("alert_history") or [],
            "system_level": level,
        }
    )


@app.route("/health", methods=["GET"])
def health():
    with _lock:
        received = _state["received_at"]
    age = None if received is None else round(time.time() - received, 1)
    return jsonify({"status": "ok", "service": "bms-cloud-dashboard", "last_push_age_seconds": age})


@app.route("/")
def dashboard():
    return send_from_directory(BASE_DIR, "live_dashboard_v3.html")


def _pi_only(_path=None):
    return jsonify({"error": "This action is only available on the Raspberry Pi."}), 501


for _p in PI_ONLY:
    app.add_url_rule(_p, endpoint="pi_only_" + _p.strip("/").replace("/", "_"),
                     view_func=_pi_only, methods=["GET", "POST"])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
