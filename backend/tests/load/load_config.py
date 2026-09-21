"""Shared settings and safety checks for the load-test kit.

Everything in backend/tests/load reads the target database from LOAD_TEST_DB_URL,
never from SUPABASE_DB_URL or backend/.env, and refuses to run against the
database the backend normally uses.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

LOAD_DIR = Path(__file__).resolve().parent
BACKEND_DIR = LOAD_DIR.parents[1]
SCHEMA_INIT_DIR = BACKEND_DIR / "schema" / "init"
MANIFEST_PATH = Path(os.environ.get("LOAD_MANIFEST", LOAD_DIR / "seed_manifest.json"))
RESULTS_DIR = LOAD_DIR / "results"

# Signs the tokens the load test uses; the stubbed app is started with the same secret.
JWT_SECRET = os.environ.get("LOAD_JWT_SECRET", "load-test-secret")

# Every row the kit creates carries one of these markers, so cleanup can never touch other data.
SEED_EMAIL_PATTERN = r"loadtest\_%@example.com"
SIGNUP_EMAIL_PATTERN = r"loadsignup\_%@example.com"
SEED_CHANNEL_PATTERN = "UCLOAD%"
PASSWORD = "LoadTest#2026"

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}


def channel_id(n: int) -> str:
    return f"UCLOAD{n:018d}"


def user_email(n: int) -> str:
    return f"loadtest_{n:05d}@example.com"


def _env_file_db_url() -> str | None:
    """The database the backend normally connects to (backend/.env), for the safety check."""
    env_file = BACKEND_DIR / ".env"
    if not env_file.exists():
        return None
    try:
        from dotenv import dotenv_values

        return dotenv_values(env_file).get("SUPABASE_DB_URL")
    except ImportError:
        return None


def _target(url: str) -> tuple[str, int | None, str]:
    parsed = urlparse(url)
    return (parsed.hostname or "", parsed.port, parsed.path.lstrip("/"))


def require_test_db_url(allow_remote: bool = False) -> str:
    """Returns LOAD_TEST_DB_URL after refusing anything that could be the real database."""
    url = os.environ.get("LOAD_TEST_DB_URL")
    if not url:
        raise SystemExit(
            "LOAD_TEST_DB_URL is not set. Point it at a database that exists only for load "
            "testing (see README.md). SUPABASE_DB_URL is deliberately not used."
        )

    protected = {u for u in (os.environ.get("SUPABASE_DB_URL"), _env_file_db_url()) if u}
    if any(_target(url) == _target(p) for p in protected):
        raise SystemExit(
            "LOAD_TEST_DB_URL points at the same database as SUPABASE_DB_URL / backend/.env. "
            "Refusing to run: the load test writes and deletes rows."
        )

    host = _target(url)[0]
    if host not in LOCAL_HOSTS and not allow_remote:
        raise SystemExit(
            f"LOAD_TEST_DB_URL host is '{host}', not local. If this is a dedicated load-test "
            "database (not production), repeat the command with --allow-remote."
        )
    return url


def describe(url: str) -> str:
    host, port, db = _target(url)
    return f"{host}:{port}/{db}"


# Pass/fail thresholds, all overridable through the environment.
def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


THRESHOLDS = {
    # read endpoints (everything except login/signup/predict)
    "read_p95_ms": _f("LOAD_READ_P95_MS", 500),
    "read_p99_ms": _f("LOAD_READ_P99_MS", 1500),
    # bcrypt-bound endpoints
    "auth_p95_ms": _f("LOAD_AUTH_P95_MS", 3000),
    # model-backed endpoint
    "predict_p95_ms": _f("LOAD_PREDICT_P95_MS", 10000),
    # whole-run error rate, as a percentage
    "max_error_pct": _f("LOAD_MAX_ERROR_PCT", 1.0),
}

# Request-name prefixes that decide which latency budget an endpoint is held to.
AUTH_ENDPOINTS = ("POST /auth/login", "POST /auth/signup", "POST /auth/change-password")
PREDICT_ENDPOINTS = ("POST /predictions (run)", "POST /forecast (run)")


def classify(name: str) -> str:
    """Which latency budget a Locust request name is held to: auth | predict | read."""
    if name.startswith(AUTH_ENDPOINTS):
        return "auth"
    if name.startswith(PREDICT_ENDPOINTS):
        return "predict"
    return "read"


def evaluate(rows: list[dict], thresholds: dict | None = None) -> list[dict]:
    """Checks endpoint rows against the thresholds.

    Each row: {name, requests, failures, p95, p99} (times in ms). Returns one verdict per
    endpoint plus a final 'ALL REQUESTS' row for the error-rate criterion; each has `passed`
    and a human-readable `detail`.
    """
    t = thresholds or THRESHOLDS
    budget = {"read": t["read_p95_ms"], "auth": t["auth_p95_ms"], "predict": t["predict_p95_ms"]}
    verdicts = []
    total_requests = total_failures = 0
    for row in rows:
        total_requests += row["requests"]
        total_failures += row["failures"]
        kind = classify(row["name"])
        problems = []
        if row["requests"] and row["p95"] > budget[kind]:
            problems.append(f"p95 {row['p95']:.0f} ms > {budget[kind]:.0f} ms")
        if kind == "read" and row["requests"] and row["p99"] > t["read_p99_ms"]:
            problems.append(f"p99 {row['p99']:.0f} ms > {t['read_p99_ms']:.0f} ms")
        verdicts.append(
            {"name": row["name"], "kind": kind, "passed": not problems, "detail": "; ".join(problems) or "ok"}
        )
    error_pct = 100.0 * total_failures / total_requests if total_requests else 0.0
    verdicts.append(
        {
            "name": "ALL REQUESTS",
            "kind": "errors",
            "passed": error_pct <= t["max_error_pct"],
            "detail": f"error rate {error_pct:.2f}% (limit {t['max_error_pct']:.2f}%)",
        }
    )
    return verdicts


def assert_stubbed_target(host: str | None) -> None:
    """Exits unless `host` is the app started from stubbed_app.py (it serves /__loadtest__).

    Every load-test tool calls this first, so a real backend (the dev server, a deployed instance)
    can never receive the test's signups and writes by accident.
    """
    import requests

    if not host:
        raise SystemExit("No target host given.")
    try:
        body = requests.get(host.rstrip("/") + "/__loadtest__", timeout=10).json()
        ok = body.get("loadtest_stub") is True
    except (requests.RequestException, ValueError, AttributeError):
        ok = False
    if not ok:
        raise SystemExit(
            f"{host} is not the load-test app (no /__loadtest__ marker). Refusing to send load or writes to it. "
            "Start the stubbed app: uvicorn stubbed_app:app --app-dir backend/tests/load --port 8100"
        )
