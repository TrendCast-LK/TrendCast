"""One request to every endpoint the load test uses, to confirm the setup works before a long run.

    python smoke_check.py [--host http://127.0.0.1:8100]

Needs the stubbed backend running and the seed data in place. Exit code 1 if anything fails.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import requests
from PIL import Image

import load_config as lc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="http://127.0.0.1:8100")
    args = parser.parse_args()

    lc.assert_stubbed_target(args.host)
    if not lc.MANIFEST_PATH.exists():
        sys.exit(f"{lc.MANIFEST_PATH} not found. Run `python seed_load_data.py seed` first.")
    manifest = json.loads(lc.MANIFEST_PATH.read_text(encoding="utf-8"))
    user = manifest["users"][0]
    channel = manifest["channels"][0]
    token = jwt.encode(
        {"sub": str(user["id"]), "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, lc.JWT_SECRET, algorithm="HS256"
    )
    auth = {"Authorization": f"Bearer {token}"}
    png = io.BytesIO()
    Image.new("RGB", (64, 64), "red").save(png, format="PNG")
    signup_email = f"loadsignup_{uuid.uuid4().hex[:12]}@example.com"

    checks = [
        ("GET /health", "GET", "/health", {}, 200),
        ("GET /forecast/health", "GET", "/forecast/health", {}, 200),
        ("GET /channels", "GET", "/channels", {}, 200),
        ("GET /channels/[id]/videos", "GET", f"/channels/{channel['channel_id']}/videos", {}, 200),
        ("GET /videos/[id]/timeseries", "GET", f"/videos/{channel['series_videos'][0]}/timeseries", {}, 200),
        ("POST /auth/login", "POST", "/auth/login", {"data": {"username": user["email"], "password": manifest["password"]}}, 200),
        ("GET /auth/me", "GET", "/auth/me", {"headers": auth}, 200),
        ("GET /dashboard/summary", "GET", "/dashboard/summary", {"headers": auth}, 200),
        ("GET /trends/summary", "GET", "/trends/summary", {"headers": auth}, 200),
        ("GET /notifications", "GET", "/notifications", {"headers": auth}, 200),
        ("POST /notifications/read-all", "POST", "/notifications/read-all", {"headers": auth}, 200),
        ("GET /channel/me", "GET", "/channel/me", {"headers": auth}, 200),
        ("GET /predictions", "GET", "/predictions", {"headers": auth}, 200),
        ("POST /predictions (draft)", "POST", "/predictions",
         {"headers": auth, "data": {"title": "smoke draft", "save_as_draft": "true"}}, 200),
        ("POST /predictions (run)", "POST", "/predictions",
         {"headers": auth, "data": {"title": "smoke run", "save_as_draft": "false", "category": "Tech"},
          "files": {"thumbnail": ("t.png", png.getvalue(), "image/png")}}, 200),
        ("POST /forecast (run)", "POST", "/forecast",
         {"json": {"title": "smoke", "channel_id": "UCLOADUSER00000000000001"}}, 200),
        ("POST /auth/signup", "POST", "/auth/signup",
         {"json": {"full_name": "Smoke", "email": signup_email, "password": lc.PASSWORD,
                   "channel_url": "https://youtube.com/@smoke"}}, 200),
    ]

    failed = 0
    print(f"{'endpoint':34s} {'status':>6} {'ms':>7}")
    for label, method, path, kwargs, expected in checks:
        start = time.perf_counter()
        try:
            response = requests.request(method, args.host + path, timeout=60, **kwargs)
            status, ok = response.status_code, response.status_code == expected
            note = "" if ok else f"  <- expected {expected}: {response.text[:150]}"
        except requests.RequestException as exc:
            status, ok, note = 0, False, f"  <- {exc}"
        failed += not ok
        print(f"{label:34s} {status:>6} {(time.perf_counter() - start) * 1000:>7.0f}{note}")

    print(f"\n{len(checks) - failed}/{len(checks)} endpoints OK")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
