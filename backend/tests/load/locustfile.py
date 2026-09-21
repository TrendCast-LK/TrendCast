"""Locust scenarios for the TrendCast API (the ETL is out of scope).

Run (web UI at http://localhost:8089):
    locust -f backend/tests/load/locustfile.py --host http://127.0.0.1:8100

Headless with one of the timed shapes (see shapes.py):
    LOAD_SHAPE=spike locust -f backend/tests/load/locustfile.py --host http://127.0.0.1:8100 --headless \
        --csv results/spike --html results/spike.html

Virtual-user types and their share of the traffic:
    BrowseUser   60  dashboard, trends, notifications, own predictions, own channel
    DataUser     25  /channels, /channels/{id}/videos, /videos/{id}/timeseries, health
    PredictUser  10  POST /predictions (model run + drafts), delete, /forecast
    AccountUser   5  signup, profile update, password change, channel refresh
    AuthStormUser 0  repeated logins (bcrypt-bound); only in LOAD_PROFILE=auth

LOAD_PROFILE picks which types run: mixed (default) | read | browse | data | predict | account | auth.
LOAD_AUTH=token (default) signs a JWT per virtual user; LOAD_AUTH=login makes each user log in first.

Requires the seed data from seed_load_data.py (seed_manifest.json).
"""

from __future__ import annotations

import io
import itertools
import json
import os
import random
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from locust import HttpUser, between, events, task
from PIL import Image

import load_config as lc
import shapes

PROFILE = os.environ.get("LOAD_PROFILE", "mixed").lower()
AUTH_MODE = os.environ.get("LOAD_AUTH", "token").lower()
TIMEOUT = float(os.environ.get("LOAD_TIMEOUT_S", 60))
THINK = (float(os.environ.get("LOAD_THINK_MIN", 1)), float(os.environ.get("LOAD_THINK_MAX", 3)))
INCLUDE_FORECAST = os.environ.get("LOAD_INCLUDE_FORECAST", "1") == "1"

PROFILES = {
    "mixed": {"BrowseUser", "DataUser", "PredictUser", "AccountUser"},
    "read": {"BrowseUser", "DataUser"},
    "browse": {"BrowseUser"},
    "data": {"DataUser"},
    "predict": {"PredictUser"},
    "account": {"AccountUser"},
    "auth": {"AuthStormUser"},
}
if PROFILE not in PROFILES:
    raise SystemExit(f"LOAD_PROFILE must be one of {sorted(PROFILES)}")

# Only one LoadTestShape may exist in this module's namespace, so bind the chosen one here.
_shape_name = os.environ.get("LOAD_SHAPE", "").lower()
if _shape_name:
    if _shape_name not in shapes.SHAPES:
        raise SystemExit(f"LOAD_SHAPE must be one of {sorted(shapes.SHAPES)}")
    SelectedShape = shapes.SHAPES[_shape_name]

if not lc.MANIFEST_PATH.exists():
    raise SystemExit(f"{lc.MANIFEST_PATH} not found. Run `python seed_load_data.py seed` first.")
MANIFEST = json.loads(lc.MANIFEST_PATH.read_text(encoding="utf-8"))
ACCOUNTS = MANIFEST["users"]
CHANNELS = MANIFEST["channels"]
_account_cycle = itertools.cycle(random.sample(ACCOUNTS, len(ACCOUNTS)))

CATEGORIES = ["Gaming", "Music", "Education", "Vlog", "Tech"]
WORDS = ["How", "Why", "Best", "Ultimate", "Guide", "Review", "Tips", "2026", "Challenge", "Setup", "Tour", "Secrets"]


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (320, 180), (random.randint(0, 255), 80, 120)).save(buffer, format="PNG")
    return buffer.getvalue()


THUMBNAILS = [_png() for _ in range(8)]  # a few distinct images, built once


def _title() -> str:
    return " ".join(random.sample(WORDS, 4))


def _sign_token(user_id: int) -> str:
    payload = {"sub": str(user_id), "exp": datetime.now(timezone.utc) + timedelta(hours=6)}
    return jwt.encode(payload, lc.JWT_SECRET, algorithm="HS256")


class TrendCastUser(HttpUser):
    abstract = True
    wait_time = between(*THINK)

    account: dict | None = None
    email: str | None = None
    password: str = lc.PASSWORD

    # ---- plumbing --------------------------------------------------------------------------
    def call(self, method: str, path: str, name: str, ok=(200,), expect=None, **kwargs):
        """One request with a stable stats name; anything outside `ok` (or failing `expect`) is a failure."""
        kwargs.setdefault("timeout", TIMEOUT)
        with self.client.request(method, path, name=name, catch_response=True, **kwargs) as response:
            if response.status_code not in ok:
                response.failure(f"HTTP {response.status_code}: {response.text[:150]}")
                return response
            if expect is not None:
                try:
                    body = response.json()
                    if not expect(body):
                        response.failure("unexpected response body")
                except ValueError:
                    response.failure("response was not JSON")
            return response

    def set_token(self, token: str) -> None:
        self.client.headers["Authorization"] = f"Bearer {token}"

    def login(self) -> bool:
        response = self.call(
            "POST", "/auth/login", "POST /auth/login",
            data={"username": self.email, "password": self.password},
            expect=lambda b: bool(b.get("access_token")),
        )
        if response.status_code == 200:
            self.set_token(response.json()["access_token"])
            return True
        return False

    def on_start(self):
        self.account = next(_account_cycle)
        self.email = self.account["email"]
        if AUTH_MODE == "login":
            if not self.login():
                self.set_token(_sign_token(self.account["id"]))  # keep the run going; the failure is recorded
        else:
            self.set_token(_sign_token(self.account["id"]))


class BrowseUser(TrendCastUser):
    """Someone using the dashboard: the read-heavy bulk of the traffic."""

    weight = 60
    prediction_ids: list[int]

    def on_start(self):
        super().on_start()
        self.prediction_ids = []
        self.call("GET", "/dashboard/summary", "GET /dashboard/summary")

    @task(5)
    def dashboard(self):
        self.call("GET", "/dashboard/summary", "GET /dashboard/summary", expect=lambda b: "subscribers" in b)

    @task(3)
    def trends(self):
        self.call("GET", "/trends/summary", "GET /trends/summary", expect=lambda b: "total_predictions" in b)

    @task(4)
    def notifications(self):
        self.call("GET", "/notifications", "GET /notifications", expect=lambda b: "unread_count" in b)

    @task(3)
    def list_predictions(self):
        response = self.call("GET", "/predictions", "GET /predictions", expect=lambda b: isinstance(b, list))
        if response.status_code == 200:
            self.prediction_ids = [p["id"] for p in response.json()][:20]

    @task(2)
    def open_prediction(self):
        if self.prediction_ids:
            self.call("GET", f"/predictions/{random.choice(self.prediction_ids)}", "GET /predictions/[id]")

    @task(2)
    def my_channel(self):
        self.call("GET", "/channel/me", "GET /channel/me")

    @task(1)
    def me(self):
        self.call("GET", "/auth/me", "GET /auth/me", expect=lambda b: "email" in b)

    @task(1)
    def mark_all_read(self):
        self.call("POST", "/notifications/read-all", "POST /notifications/read-all")


class DataUser(TrendCastUser):
    """Reads the pipeline data. Public endpoints in the API, so no token is needed (it is sent anyway)."""

    weight = 25

    @task(2)
    def channels(self):
        # returns every channel in one response, so this is the largest payload in the mix
        self.call("GET", "/channels", "GET /channels", expect=lambda b: isinstance(b, list))

    @task(4)
    def channel_videos(self):
        channel = random.choice(CHANNELS)
        self.call("GET", f"/channels/{channel['channel_id']}/videos", "GET /channels/[id]/videos",
                  expect=lambda b: isinstance(b, list))

    @task(4)
    def timeseries(self):
        channel = random.choice(CHANNELS)
        if channel["series_videos"]:
            video_id = random.choice(channel["series_videos"])
            self.call("GET", f"/videos/{video_id}/timeseries", "GET /videos/[id]/timeseries",
                      expect=lambda b: isinstance(b, list) and len(b) > 0)

    @task(1)
    def health(self):
        self.call("GET", "/health", "GET /health", expect=lambda b: b.get("db") == "connected")

    @task(1)
    def forecast_health(self):
        self.call("GET", "/forecast/health", "GET /forecast/health")


class PredictUser(TrendCastUser):
    """Runs forecasts (the expensive operation) and saves drafts."""

    weight = 10
    wait_time = between(THINK[0] + 2, THINK[1] + 5)  # nobody re-runs a forecast every second
    created: list[int]

    def on_start(self):
        super().on_start()
        self.created = []

    def _form(self, draft: bool) -> dict:
        return {
            "title": _title(),
            "category": random.choice(CATEGORIES),
            "tags": ",".join(random.sample(WORDS, 3)),
            "target_date": (datetime.now(timezone.utc) + timedelta(days=random.randint(1, 30))).strftime("%Y-%m-%d"),
            "target_time": "18:00",
            "duration": "PT8M30S",
            "save_as_draft": "true" if draft else "false",
        }

    @task(3)
    def run_prediction(self):
        files = {"thumbnail": ("thumb.png", random.choice(THUMBNAILS), "image/png")}
        response = self.call(
            "POST", "/predictions", "POST /predictions (run)",
            data=self._form(draft=False), files=files,
            expect=lambda b: b.get("status") == "complete" and b.get("predicted_views") is not None,
        )
        if response.status_code == 200:
            self.created.append(response.json()["id"])

    @task(2)
    def save_draft(self):
        response = self.call(
            "POST", "/predictions", "POST /predictions (draft)",
            data=self._form(draft=True), expect=lambda b: b.get("status") == "draft",
        )
        if response.status_code == 200:
            self.created.append(response.json()["id"])

    @task(2)
    def list_predictions(self):
        self.call("GET", "/predictions", "GET /predictions", expect=lambda b: isinstance(b, list))

    @task(1)
    def delete_one(self):
        if self.created:
            self.call("DELETE", f"/predictions/{self.created.pop()}", "DELETE /predictions/[id]", ok=(204,))

    if INCLUDE_FORECAST:
        @task(1)
        def forecast_endpoint(self):
            self.call(
                "POST", "/forecast", "POST /forecast (run)",
                json={"title": _title(), "channel_id": "UCLOADUSER00000000000001",
                      "scheduled_upload_time": datetime.now(timezone.utc).isoformat()},
                expect=lambda b: b.get("status") == "ok",
            )


class AccountUser(TrendCastUser):
    """Creates its own account (bcrypt + channel lookup + two notifications), then edits it."""

    weight = 5
    wait_time = between(THINK[0] + 1, THINK[1] + 3)

    def on_start(self):
        # deliberately not the base on_start: this user signs up instead of using a seeded account
        self.email = f"loadsignup_{uuid.uuid4().hex[:16]}@example.com"
        self.password = lc.PASSWORD
        response = self.call(
            "POST", "/auth/signup", "POST /auth/signup",
            json={"full_name": "Load Signup", "email": self.email, "password": self.password,
                  "channel_url": f"https://youtube.com/@{uuid.uuid4().hex[:10]}"},
            expect=lambda b: bool(b.get("access_token")),
        )
        self.signed_up = response.status_code == 200
        if self.signed_up:
            self.set_token(response.json()["access_token"])

    @task(3)
    def update_profile(self):
        if self.signed_up:
            self.call("PATCH", "/auth/me", "PATCH /auth/me",
                      json={"subscribers": random.randint(0, 900000), "monthly_views": random.randint(0, 9000000)})

    @task(2)
    def me(self):
        if self.signed_up:
            self.call("GET", "/auth/me", "GET /auth/me")

    @task(1)
    def refresh_channel(self):
        if self.signed_up:
            self.call("POST", "/channel/refresh", "POST /channel/refresh")

    @task(1)
    def change_password(self):
        if self.signed_up:
            new_password = f"New{uuid.uuid4().hex[:10]}!"
            response = self.call(
                "POST", "/auth/change-password", "POST /auth/change-password",
                json={"current_password": self.password, "new_password": new_password},
            )
            if response.status_code == 200:
                self.password = new_password

    @task(1)
    def login_again(self):
        if self.signed_up:
            self.login()


class AuthStormUser(TrendCastUser):
    """Back-to-back logins by seeded accounts: measures how bcrypt saturates the CPU."""

    weight = 1
    wait_time = between(0.1, 0.5)

    @task
    def login_only(self):
        self.login()

    @task
    def login_then_read(self):
        if self.login():
            self.call("GET", "/dashboard/summary", "GET /dashboard/summary")


for _cls in (BrowseUser, DataUser, PredictUser, AccountUser, AuthStormUser):
    _cls.abstract = _cls.__name__ not in PROFILES[PROFILE]
del _cls  # a leftover module-level alias would make Locust see the last class twice


# ---- target check -----------------------------------------------------------------------------
@events.test_start.add_listener
def refuse_wrong_target(environment, **_kwargs):
    """The test signs up users and writes predictions: only ever run it against the stubbed app."""
    if os.environ.get("LOAD_SKIP_TARGET_CHECK") == "1":
        return
    try:
        lc.assert_stubbed_target(environment.host)
    except SystemExit as exc:
        print(f"\n{exc}\n")
        environment.process_exit_code = 2
        environment.runner.quit()


# ---- pass/fail --------------------------------------------------------------------------------
@events.quitting.add_listener
def check_criteria(environment, **_kwargs):
    stats = environment.stats
    rows = [
        {
            "name": entry.name,  # request names already start with the HTTP method
            "requests": entry.num_requests,
            "failures": entry.num_failures,
            "p95": entry.get_response_time_percentile(0.95) or 0,
            "p99": entry.get_response_time_percentile(0.99) or 0,
        }
        for entry in stats.entries.values()
    ]
    verdicts = lc.evaluate(rows)
    print("\n=== Load test criteria ===")
    for verdict in verdicts:
        print(f"  [{'PASS' if verdict['passed'] else 'FAIL'}] {verdict['name']:38s} {verdict['detail']}")
    # explicit either way: otherwise Locust exits 1 on any single failed request, whatever the limits say
    environment.process_exit_code = 0 if all(v["passed"] for v in verdicts) else 1
