"""
Runs on the Raspberry Pi. Copies the local backend's /api/telemetry result to
the Render dashboard. All processing stays on the Pi; this only forwards it.

Environment (see render.env):
  RENDER_URL     e.g. https://bms-dashboard.onrender.com
  INGEST_TOKEN   same secret as on Render
  LOCAL_URL      default http://127.0.0.1:5000/api/telemetry
  PUSH_INTERVAL  seconds between pushes, default 2
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

LOCAL_URL = os.environ.get("LOCAL_URL", "http://127.0.0.1:5000/api/telemetry")
RENDER_URL = os.environ.get("RENDER_URL", "").rstrip("/")
TOKEN = os.environ.get("INGEST_TOKEN", "")
INTERVAL = float(os.environ.get("PUSH_INTERVAL", "2"))


def log(msg):
    print(time.strftime("[%Y-%m-%d %H:%M:%S]"), msg, flush=True)


def main():
    if not RENDER_URL or not TOKEN:
        log("RENDER_URL and INGEST_TOKEN must be set.")
        sys.exit(1)
    target = RENDER_URL + "/api/ingest"
    log(f"Forwarding {LOCAL_URL} -> {target} every {INTERVAL}s")
    failures = 0
    while True:
        started = time.time()
        try:
            with urllib.request.urlopen(LOCAL_URL, timeout=8) as r:
                body = r.read()
            req = urllib.request.Request(
                target,
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + TOKEN,
                },
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                r.read()
            if failures:
                log(f"Recovered after {failures} failed attempt(s).")
            failures = 0
        except (urllib.error.URLError, OSError, ValueError) as e:
            failures += 1
            if failures in (1, 5) or failures % 30 == 0:
                log(f"Push failed ({failures} in a row): {e}")
        time.sleep(max(0.2, INTERVAL - (time.time() - started)) if failures == 0
                   else min(30, INTERVAL * (1 + failures)))


if __name__ == "__main__":
    main()
