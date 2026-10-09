"""Failover and recovery tests (section 3.1.7 of the test plan).

The plan is FAILOVER_RECOVERY_TEST_PLAN.md; each test names the case it covers (F-nn).
Faults are injected into disposable infrastructure only:

* a dedicated Postgres container that these tests kill, restart and pause (`flaky_pg`);
  the shared session server is never restarted because other tests use it;
* fakes for YouTube, the model and the disk.

Not automated (manual, see the plan): F-01 kill the real API process mid-request, F-12 and F-13 browser
behaviour, F-17 is covered here only in miniature (the full spike/ramp run lives in tests/load/).
Tests marked xfail(strict=True) are defects found by this suite, with the reason attached.
"""

import errno
import json
import pathlib
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace

import psycopg2
import pytest
import requests
from psycopg2 import errors, sql
from psycopg2.extensions import make_dsn
from psycopg2.pool import ThreadedConnectionPool

import db_helpers as h
from conftest import BACKEND_DIR, CONTAINER, INIT_SCRIPTS, MIGRATION_003, PG_IMAGE, apply_sql
from test_api_data import CHANNEL, FORECAST, PASSWORD, fake_forecast, fake_youtube, make_user, post_prediction, png_bytes, rows, signup  # noqa: F401
from test_api_function import _real_inference, bearer
from test_backup_restore import in_container, seed_every_table

# ---------------------------------------------------------------------------
# A Postgres server the tests are allowed to break
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FlakyPg:
    """Docker-managed Postgres on a fixed port (a random port would change on restart)."""

    def __init__(self, name: str, port: int):
        self.name, self.port = name, port

    def kwargs(self, dbname="postgres") -> dict:
        return {"host": "127.0.0.1", "port": self.port, "user": "postgres", "password": "test", "dbname": dbname}

    def dsn(self, dbname="postgres") -> str:
        return make_dsn(**self.kwargs(dbname))

    def connect(self, dbname="postgres"):
        return psycopg2.connect(**self.kwargs(dbname))

    def _docker(self, *args):
        subprocess.run(["docker", *args, self.name], check=True, capture_output=True, timeout=120)

    def wait_ready(self, timeout=90):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                self.connect().close()
                return
            except psycopg2.OperationalError:
                time.sleep(0.5)
        raise RuntimeError("flaky Postgres did not come back in time")

    def kill(self):  # SIGKILL: an unclean shutdown, like a power cut
        self._docker("kill")

    def start(self):
        self._docker("start")
        self.wait_ready()

    def restart(self):
        self._docker("restart")
        self.wait_ready()

    def pause(self):  # processes frozen, TCP connections stay open: a hung server
        self._docker("pause")

    def unpause(self):
        self._docker("unpause")

    def ensure_up(self):
        state = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}", self.name], capture_output=True, text=True
        ).stdout.strip()
        if state == "paused":
            self.unpause()
        elif state != "running":
            self._docker("start")
        self.wait_ready()

    def create_database(self) -> str:
        name = f"tc_{uuid.uuid4().hex[:12]}"
        admin = self.connect()
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(sql.SQL("CREATE DATABASE {} TEMPLATE tc_flaky_template").format(sql.Identifier(name)))
        admin.close()
        return name


@pytest.fixture(scope="module")
def flaky_pg():
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("Docker is not available")
    name, port = f"trendcast-flaky-{uuid.uuid4().hex[:8]}", _free_port()
    subprocess.run(
        ["docker", "run", "-d", "--name", name, "-e", "POSTGRES_PASSWORD=test",
         "-p", f"127.0.0.1:{port}:5432", PG_IMAGE],
        check=True, capture_output=True,
    )
    pg = FlakyPg(name, port)
    try:
        pg.wait_ready()
        admin = pg.connect()
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute("CREATE DATABASE tc_flaky_template")
        admin.close()
        template = pg.connect("tc_flaky_template")
        for script in INIT_SCRIPTS:
            apply_sql(template, script)
        template.close()
        yield pg
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


@pytest.fixture
def flaky(flaky_pg):
    flaky_pg.ensure_up()
    yield flaky_pg
    flaky_pg.ensure_up()  # whatever a test did, the next one starts with a running server


@pytest.fixture
def flaky_db(flaky):
    """(FlakyPg, database name) with the full schema."""
    return SimpleNamespace(pg=flaky, name=flaky.create_database())


@pytest.fixture
def flaky_api(backend, flaky_db, monkeypatch, tmp_path):
    """The real routers with the backend's pool pointed at the breakable server."""
    from fastapi.testclient import TestClient

    pool = ThreadedConnectionPool(1, backend.db.MAX_CONNECTIONS, **flaky_db.pg.kwargs(flaky_db.name))
    monkeypatch.setattr(backend.db, "pool", pool)
    monkeypatch.setattr(backend.storage, "UPLOADS_DIR", tmp_path)
    monkeypatch.setattr(backend.channel_router, "resolve_channel", lambda url: dict(CHANNEL))
    client = TestClient(backend.app, raise_server_exceptions=False)
    yield SimpleNamespace(client=client, db=backend.db, pg=flaky_db.pg, dbname=flaky_db.name, uploads=tmp_path)
    try:
        pool.closeall()
    except Exception:  # noqa: BLE001  (connections may be dead by design)
        pass


def run_threads(target, count):
    results, errors_ = [None] * count, []

    def worker(index):
        try:
            results[index] = target(index)
        except Exception as exc:  # noqa: BLE001
            errors_.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    return results, errors_


def full_pool_free(db):
    """True when every connection slot and every pooled connection has been handed back."""
    return db._slots._value == db.MAX_CONNECTIONS and not db.pool._used


# ---------------------------------------------------------------------------
# F-02  Interrupted transactions: a crash must not leave half a write
# ---------------------------------------------------------------------------

def test_f02_a_crash_mid_transaction_rolls_back_only_the_uncommitted_work(flaky_db):
    pg = flaky_db.pg
    conn = pg.connect(flaky_db.name)
    with conn.cursor() as cur:
        h.user(cur, email="committed@example.com")
    conn.commit()
    with conn.cursor() as cur:  # in flight when the server dies
        uid = h.user(cur, email="in-flight@example.com")
        h.notification(cur, user_id=uid)
        h.prediction(cur, user_id=uid, title="in flight")

    pg.kill()
    pg.start()

    after = pg.connect(flaky_db.name)
    with after.cursor() as cur:
        cur.execute("SELECT email FROM users ORDER BY 1")
        assert cur.fetchall() == [("committed@example.com",)]
        assert h.count(cur, "notifications") == 0 and h.count(cur, "predictions") == 0
    after.close()


def test_f02_committed_data_survives_a_crash(flaky_db):
    pg = flaky_db.pg
    conn = pg.connect(flaky_db.name)
    with conn.cursor() as cur:
        seed_every_table(cur)
    conn.commit()
    with conn.cursor() as cur:
        before = h.snapshot_rows(cur, ["users", "predictions", "notifications", "channel_history_videos"])
    conn.close()

    pg.kill()
    pg.start()

    after = pg.connect(flaky_db.name)
    with after.cursor() as cur:
        assert h.snapshot_rows(cur, ["users", "predictions", "notifications", "channel_history_videos"]) == before
    after.close()


def test_f02_signup_failure_after_the_user_row_leaves_an_account_that_can_still_log_in(api):
    def boom(*args, **kwargs):
        raise RuntimeError("database went away")

    api.monkeypatch.setattr(sys.modules["routers.auth"], "create_notification", boom)
    assert signup(api).status_code == 500  # client sees a failure
    assert len(rows(api, "SELECT id FROM users")) == 1
    login = api.client.post("/auth/login", data={"username": "ann@example.com", "password": PASSWORD})
    assert login.status_code == 200  # ...but the account is usable, so a retry is not a dead end


@pytest.mark.xfail(strict=True, reason="signup commits the user row and then writes the welcome notification in a "
                                       "separate transaction, so a failure between them leaves a user without one "
                                       "and makes a client retry fail with 'account already exists'")
def test_f02_signup_is_all_or_nothing(api):
    def boom(*args, **kwargs):
        raise RuntimeError("database went away")

    api.monkeypatch.setattr(sys.modules["routers.auth"], "create_notification", boom)
    signup(api)
    users = rows(api, "SELECT id FROM users")
    welcomes = rows(api, "SELECT id FROM notifications WHERE type = 'welcome'")
    assert len(users) == len(welcomes)  # both or neither


def test_f02_a_failed_notification_does_not_fail_a_saved_prediction(api):
    _, headers = make_user(api)
    fake_forecast(api)

    def boom(*args, **kwargs):
        raise RuntimeError("notifications table unavailable")

    api.monkeypatch.setattr(api.backend.predictions_router, "create_notification", boom)
    response = post_prediction(api, headers)
    assert response.status_code == 200
    assert len(rows(api, "SELECT id FROM predictions WHERE status = 'complete'")) == 1


# ---------------------------------------------------------------------------
# F-03 / F-04 / F-05  The database goes away and comes back
# ---------------------------------------------------------------------------

def warm_pool(db):
    """Runs MAX_CONNECTIONS overlapping requests. psycopg2's pool keeps only `minconn` idle connections
    afterwards, so exactly one live (soon to be stale) connection is left behind."""
    def hold(_):
        with db.get_cursor() as cur:
            cur.execute("SELECT pg_sleep(0.4)")

    _, errors_ = run_threads(hold, db.MAX_CONNECTIONS)
    assert errors_ == []
    assert len(db.pool._pool) >= 1


def attempts_until_success(db, limit):
    outcomes = []
    for _ in range(limit):
        try:
            with db.get_cursor() as cur:
                cur.execute("SELECT 1")
            outcomes.append(True)
            break
        except Exception as exc:  # noqa: BLE001
            outcomes.append(type(exc).__name__)
        time.sleep(0.05)
    return outcomes


def test_f03_pool_heals_itself_after_a_database_restart(flaky_api):
    warm_pool(flaky_api.db)
    flaky_api.pg.restart()
    outcomes = attempts_until_success(flaky_api.db, limit=3)
    assert outcomes[-1] is True, f"never recovered: {outcomes}"
    assert full_pool_free(flaky_api.db)


@pytest.mark.xfail(strict=True, reason="the idle pooled connection that died with the restart is handed out without "
                                       "being checked, so the first request after a restart fails even though "
                                       "the database is already back")
def test_f03_first_request_after_a_restart_succeeds(flaky_api):
    warm_pool(flaky_api.db)
    flaky_api.pg.restart()
    assert attempts_until_success(flaky_api.db, limit=1) == [True]


def test_f03_http_requests_recover_after_a_restart_without_restarting_the_backend(flaky_api):
    _, headers = make_user(flaky_api)
    warm_pool(flaky_api.db)
    flaky_api.pg.restart()

    statuses = []
    for _ in range(flaky_api.db.MAX_CONNECTIONS + 3):
        statuses.append(flaky_api.client.get("/dashboard/summary", headers=headers).status_code)
        if statuses[-1] == 200:
            break
    assert statuses[-1] == 200, statuses
    assert set(statuses) <= {200, 500}  # a failure is a plain 500, never a hang or another code
    # and it stays healthy
    assert [flaky_api.client.get("/dashboard/summary", headers=headers).status_code for _ in range(15)] == [200] * 15


def test_f03_users_and_tokens_survive_the_restart(flaky_api):
    _, headers = make_user(flaky_api)
    flaky_api.pg.kill()
    flaky_api.pg.start()
    statuses = [flaky_api.client.get("/auth/me", headers=headers).status_code for _ in range(flaky_api.db.MAX_CONNECTIONS + 2)]
    assert statuses[-1] == 200  # same token, same user: nothing was lost


def test_f03_requests_fail_fast_while_the_database_is_down(flaky_api):
    _, headers = make_user(flaky_api)
    warm_pool(flaky_api.db)
    flaky_api.pg.kill()
    started = time.time()
    statuses = [flaky_api.client.get("/dashboard/summary", headers=headers).status_code for _ in range(3)]
    assert statuses == [500, 500, 500]
    assert time.time() - started < 20  # an error, not a hang
    assert full_pool_free(flaky_api.db)  # nothing leaked while failing


def run_backend_import(dsn):
    """Imports backend/db.py in a fresh interpreter, as `uvicorn main:app` does on startup."""
    env = {
        **__import__("os").environ,
        "SUPABASE_DB_URL": dsn, "JWT_SECRET_KEY": "x", "YOUTUBE_API_KEY": "x",
    }
    return subprocess.run(
        [sys.executable, "-c", "import db; print('imported')"],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=60,
    )


def test_f04_backend_refuses_to_start_when_the_database_is_down_and_starts_once_it_is_back(flaky):
    flaky.kill()
    started = time.time()
    down = run_backend_import(flaky.dsn())
    assert down.returncode != 0
    assert "OperationalError" in down.stderr and "connect" in down.stderr.lower()  # a clear reason, not a hang
    assert time.time() - started < 30

    flaky.start()
    up = run_backend_import(flaky.dsn())
    assert up.returncode == 0, up.stderr  # no manual clean-up needed
    assert "imported" in up.stdout


def test_f05_a_request_waits_then_fails_cleanly_when_every_connection_is_busy(api):
    db = api.backend.db
    api.monkeypatch.setattr(db, "CONNECTION_WAIT_SECONDS", 1)
    with ExitStack() as stack:
        for _ in range(db.MAX_CONNECTIONS):
            stack.enter_context(db.get_connection())
        started = time.time()
        with pytest.raises(RuntimeError, match="timed out waiting"):
            with db.get_connection():
                pass
        assert 0.9 < time.time() - started < 5
    assert full_pool_free(db)
    with db.get_cursor() as cur:  # and the pool is usable again
        cur.execute("SELECT 1")
        assert cur.fetchone() == (1,)


def test_f05_a_hung_database_stalls_requests_and_they_complete_when_it_returns(flaky_api):
    db = flaky_api.db
    with db.get_cursor() as cur:  # warm one connection
        cur.execute("SELECT 1")
    result = {}

    def query():
        started = time.time()
        try:
            with db.get_cursor() as cur:
                cur.execute("SELECT 42")
                result["value"] = cur.fetchone()[0]
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc
        result["seconds"] = time.time() - started

    flaky_api.pg.pause()
    try:
        thread = threading.Thread(target=query)
        thread.start()
        time.sleep(4)
        stalled = thread.is_alive()  # the backend sets no query timeout: this is what happens today
    finally:
        flaky_api.pg.unpause()
    thread.join(timeout=60)

    assert stalled, "expected the query to be blocked while the database is frozen"
    assert result.get("value") == 42, result  # completes, does not error, once the server is back
    assert result["seconds"] >= 4
    assert full_pool_free(db)


# ---------------------------------------------------------------------------
# F-17  Overload, then recovery (miniature of the tests/load spike scenario)
# ---------------------------------------------------------------------------

def test_f17_pool_recovers_after_overload_with_timeouts_and_errors(api):
    db = api.backend.db
    api.monkeypatch.setattr(db, "CONNECTION_WAIT_SECONDS", 0.3)

    def request(index):
        with db.get_cursor() as cur:
            cur.execute("SELECT pg_sleep(0.25)" if index % 3 else "SELECT 1/0")

    _, failures = run_threads(request, db.MAX_CONNECTIONS * 4)
    assert failures  # the overload really did cause timeouts and SQL errors
    assert {type(f) for f in failures} <= {RuntimeError, errors.DivisionByZero}

    assert full_pool_free(db)
    started = time.time()
    with db.get_cursor() as cur:
        cur.execute("SELECT 1")
    assert time.time() - started < 1  # back to normal at once, no restart needed


# ---------------------------------------------------------------------------
# F-09  Model unavailable
# ---------------------------------------------------------------------------

REQUIRED_ARTIFACTS = [
    "catboost_magnitude.cbm", "catboost_shape_form.cbm", "catboost_shape_c.cbm", "catboost_shape_theta.cbm",
    "catboost_shape_k.cbm", "catboost_shape_t0.cbm", "pca_text.pkl", "pca_image.pkl",
    "feature_columns.json", "maturation_curve.json", "config.json",
    "histattn_v2.pt", "histattn_scaler.pkl", "histattn_config.json", "histattn_tab_columns.json",
]


@pytest.fixture(scope="module")
def real_inference():
    return _real_inference()  # importing torch and friends takes a while: do it once


@pytest.fixture
def broken_model_dir(tmp_path, real_inference, monkeypatch):
    """A stand-in artifact folder (the loader checks presence and reads the JSON/PCA files before any
    model, so tiny placeholders reproduce the failures without the 1 GB of real artifacts)."""
    inference = real_inference
    monkeypatch.setattr(inference, "_state", inference._state)  # restored afterwards
    target = tmp_path / "artifacts"
    target.mkdir()
    for name in REQUIRED_ARTIFACTS:
        (target / name).write_bytes(b"{}" if name.endswith(".json") else b"placeholder")
    monkeypatch.setattr(inference, "ARTIFACTS_DIR", target)
    return inference, target


def test_f09_loading_never_raises_and_reports_a_missing_artifact(broken_model_dir):
    inference, target = broken_model_dir
    (target / "catboost_magnitude.cbm").unlink()
    inference.load_artifacts()  # the app must still start
    state = inference.get_state()
    assert state.ready is False
    assert "missing artifact" in state.error and "catboost_magnitude.cbm" in state.error


def test_f09_a_corrupt_artifact_is_reported_not_raised(broken_model_dir):
    inference, target = broken_model_dir
    (target / "pca_text.pkl").write_bytes(b"not a pickle")
    inference.load_artifacts()
    state = inference.get_state()
    assert state.ready is False and state.error  # names the exception
    assert state.load_time_seconds is not None


def test_f09_with_the_model_down_drafts_still_save_and_completed_runs_fail_cleanly_then_work_again(api):
    _, headers = make_user(api)
    fake_forecast(api, raises=RuntimeError("model artifacts unavailable"))

    down = post_prediction(api, headers)
    assert down.status_code == 503 and "unavailable" in down.json()["detail"]
    assert rows(api, "SELECT id FROM predictions") == []
    assert list(api.uploads.iterdir()) == []  # the thumbnail was not left behind

    assert post_prediction(api, headers, draft=True).status_code == 200  # degraded, not dead
    assert api.client.get("/predictions", headers=headers).status_code == 200

    fake_forecast(api)  # the model is back
    assert post_prediction(api, headers).status_code == 200
    assert [s for (s,) in rows(api, "SELECT status FROM predictions ORDER BY id")] == ["draft", "complete"]


def test_f09_forecast_endpoint_reports_the_model_error_and_health_keeps_working(api):
    import main
    from fastapi.testclient import TestClient

    class Broken:
        ready, error, device, load_time_seconds = False, "FileNotFoundError: pca_text.pkl", "cpu", None

    api.monkeypatch.setattr(main, "get_state", lambda: Broken())
    client = TestClient(main.app, raise_server_exceptions=False)
    assert client.get("/forecast/health").json()["ready"] is False
    assert client.post("/forecast", json={"title": "t"}).status_code == 503
    assert client.get("/health").json() == {"status": "ok", "db": "connected"}


# ---------------------------------------------------------------------------
# F-10  YouTube unavailable
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fault", [requests.Timeout("read timed out"), requests.ConnectionError("dns failure"), "http500", "http403"])
def test_f10_youtube_transport_faults_become_a_resolution_error(fault, monkeypatch):
    import youtube

    def fake_get(*args, **kwargs):
        if isinstance(fault, Exception):
            raise fault
        return SimpleNamespace(status_code=500 if fault == "http500" else 403, json=lambda: {})

    monkeypatch.setattr(youtube.requests, "get", fake_get)
    with pytest.raises(youtube.YouTubeResolutionError):
        youtube.resolve_channel("https://youtube.com/@someone")


def fail_youtube_now(api, message="could not reach the YouTube API: timed out"):
    error = api.backend.channel_router.YouTubeResolutionError

    def _raise(url):
        raise error(message)

    api.monkeypatch.setattr(api.backend.channel_router, "resolve_channel", _raise)


def test_f10_signup_survives_a_youtube_outage_and_a_later_refresh_repairs_it(api):
    fail_youtube_now(api)
    response = signup(api)
    assert response.status_code == 200  # the account is created regardless
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    ((data, error),) = rows(api, "SELECT channel_data, channel_fetch_error FROM users")
    assert data is None and "YouTube" in error
    assert "channel_fetch_error" in [t for (t,) in rows(api, "SELECT type FROM notifications")]
    api.conn.rollback()

    api.monkeypatch.setattr(api.backend.channel_router, "resolve_channel", lambda url: dict(CHANNEL))  # API is back
    assert api.client.post("/channel/refresh", headers=headers).status_code == 200
    ((data, error),) = rows(api, "SELECT channel_data, channel_fetch_error FROM users")
    assert data["channel_id"] == CHANNEL["channel_id"] and error is None


def test_f10_a_failed_refresh_keeps_the_last_good_snapshot(api):
    _, headers = make_user(api)
    fail_youtube_now(api)
    api.client.post("/channel/refresh", headers=headers)
    api.conn.rollback()
    ((data, error),) = rows(api, "SELECT channel_data, channel_fetch_error FROM users")
    assert data["channel_id"] == CHANNEL["channel_id"] and error


def test_f10_a_youtube_quota_error_during_a_forecast_is_a_503_and_saves_nothing(api):
    _, headers = make_user(api)
    fake_forecast(api, raises=api.backend.inference.QuotaExceededError("quota"))
    response = post_prediction(api, headers)
    assert response.status_code == 503 and "temporarily unavailable" in response.json()["detail"]
    assert rows(api, "SELECT id FROM predictions") == [] and list(api.uploads.iterdir()) == []


# ---------------------------------------------------------------------------
# F-11  Disk full / unwritable uploads
# ---------------------------------------------------------------------------

@pytest.fixture
def disk_full(api):
    """While active, writing into the uploads folder fails with ENOSPC."""
    real = pathlib.Path.write_bytes
    state = {"on": True, "fail_after": 0, "written": 0}

    def write_bytes(self, data):
        if state["on"] and self.parent == api.uploads:
            if state["written"] >= state["fail_after"]:
                raise OSError(errno.ENOSPC, "No space left on device")
            state["written"] += 1
        return real(self, data)

    api.monkeypatch.setattr(pathlib.Path, "write_bytes", write_bytes)
    return state


def post_with_dataset(api, headers):
    return api.client.post(
        "/predictions",
        data={"title": "My next video", "save_as_draft": "false"},
        files={"thumbnail": ("t.png", png_bytes(), "image/png"), "dataset": ("d.csv", b"a,b\n1,2\n", "text/csv")},
        headers=headers,
    )


def test_f11_a_full_disk_fails_the_request_and_recovers_when_space_returns(api, disk_full):
    _, headers = make_user(api)
    fake_forecast(api)
    assert post_prediction(api, headers).status_code == 500
    assert rows(api, "SELECT id FROM predictions") == [] and list(api.uploads.iterdir()) == []

    disk_full["on"] = False  # space freed
    assert post_prediction(api, headers).status_code == 200
    ((path,),) = rows(api, "SELECT thumbnail_path FROM predictions")
    assert (api.uploads / path.rsplit("/", 1)[1]).exists()


def test_f11_a_disk_that_fills_between_two_files_leaves_no_orphan_and_no_row(api, disk_full):
    _, headers = make_user(api)
    fake_forecast(api)
    disk_full["fail_after"] = 1  # the thumbnail is written, the dataset is not
    assert post_with_dataset(api, headers).status_code == 500
    assert list(api.uploads.iterdir()) == []  # the thumbnail was cleaned up
    assert rows(api, "SELECT id FROM predictions") == []


def test_f11_a_full_disk_does_not_affect_draft_only_data_or_reads(api, disk_full):
    _, headers = make_user(api)
    assert post_prediction(api, headers, draft=True, thumbnail=False).status_code == 200  # nothing to write
    assert api.client.get("/predictions", headers=headers).status_code == 200


# ---------------------------------------------------------------------------
# F-14  Corrupted data
# ---------------------------------------------------------------------------

def plant(api, statement, params=()):
    """Writes rows the schema would normally forbid (FK and triggers off for this session)."""
    api.cur.execute("SET session_replication_role = replica")
    api.cur.execute(statement, params)
    api.cur.execute("SET session_replication_role = DEFAULT")
    api.conn.commit()


def test_f14_a_completed_prediction_with_missing_results_does_not_break_reads(api):
    user_id, headers = make_user(api)
    plant(api, "INSERT INTO predictions (user_id, title, status) VALUES (%s, 'broken', 'complete')", (user_id,))
    assert api.client.get("/predictions", headers=headers).status_code == 200
    assert api.client.get("/dashboard/summary", headers=headers).status_code == 200
    assert api.client.get("/trends/summary", headers=headers).status_code == 200


@pytest.mark.xfail(strict=True, reason="a users.channel_data value that is not the expected JSON object (or has a "
                                       "wrong-typed field) makes /auth/me and /channel/me fail with an unhandled 500")
@pytest.mark.parametrize("bad", ['"a string"', '[1, 2]', '{"channel_id": 12345}'], ids=["string", "array", "wrong-type"])
def test_f14_malformed_channel_data_gives_a_controlled_response(api, bad):
    user_id, headers = make_user(api)
    plant(api, "UPDATE users SET channel_data = %s::jsonb WHERE id = %s", (bad, user_id))
    for path in ("/auth/me", "/dashboard/summary", "/channel/me", "/predictions"):
        assert api.client.get(path, headers=headers).status_code == 200, path


def test_f14_one_users_corrupt_row_does_not_affect_other_users(api):
    bad_id, _ = make_user(api, email="bad@example.com")
    _, good_headers = make_user(api, email="good@example.com")
    plant(api, "UPDATE users SET channel_data = '[1, 2]'::jsonb WHERE id = %s", (bad_id,))
    assert api.client.get("/dashboard/summary", headers=good_headers).status_code == 200
    fake_forecast(api)
    assert post_prediction(api, good_headers).status_code == 200


# ---------------------------------------------------------------------------
# F-15  Total loss, then restore from backup
# ---------------------------------------------------------------------------

def test_f15_after_losing_every_row_a_restored_backup_lets_the_same_users_back_in(api, pg_server, create_database):
    if not CONTAINER["name"]:
        pytest.skip("restore drill needs the Docker-managed test server")
    user_id, headers = make_user(api)
    fake_forecast(api)
    assert post_prediction(api, headers).status_code == 200
    source = api.conn.info.dbname

    dump = f"/tmp/{uuid.uuid4().hex}.dump"
    in_container("pg_dump", "-U", "postgres", "-Fc", "-f", dump, source)

    api.cur.execute("TRUNCATE users, predictions, notifications, channel_history_cache, channel_history_videos, "
                    "admins, admin_audit_log CASCADE")
    api.conn.commit()
    lost = api.client.post("/auth/login", data={"username": "ann@example.com", "password": PASSWORD})
    assert lost.status_code == 401  # the loss is real
    assert api.client.get("/auth/me", headers=headers).status_code == 401

    restored = create_database(from_template=False)
    in_container("pg_restore", "-U", "postgres", "--exit-on-error", "-d", restored.info.dbname, dump)
    in_container("rm", "-f", dump)

    pool = ThreadedConnectionPool(1, api.backend.db.MAX_CONNECTIONS, **{**pg_server, "dbname": restored.info.dbname})
    api.monkeypatch.setattr(api.backend.db, "pool", pool)
    try:
        login = api.client.post("/auth/login", data={"username": "ann@example.com", "password": PASSWORD})
        assert login.status_code == 200 and login.json()["user"]["id"] == user_id
        assert api.client.get("/auth/me", headers=headers).status_code == 200  # the old token still works
        predictions = api.client.get("/predictions", headers=headers).json()
        assert [p["status"] for p in predictions] == ["complete"]
        assert api.client.get("/notifications", headers=headers).json()["notifications"]
    finally:
        pool.closeall()


# ---------------------------------------------------------------------------
# F-16  A migration that fails halfway
# ---------------------------------------------------------------------------

def test_f16_a_failing_migration_leaves_the_database_unchanged_and_can_be_rerun_after_the_fix(conn, cur):
    cur.execute("ALTER TABLE notifications DROP CONSTRAINT chk_notifications_type")
    uid = h.user(cur)
    h.notification(cur, user_id=uid, type="bogus")
    conn.commit()
    before = h.schema_fingerprint(cur)

    with pytest.raises(errors.CheckViolation):
        apply_sql(conn, MIGRATION_003)
    conn.rollback()

    assert h.diff_schema(before, h.schema_fingerprint(cur)) == []  # nothing half-applied
    assert h.count(cur, "notifications", "type = 'bogus'") == 1  # no data touched

    cur.execute("UPDATE notifications SET type = 'welcome' WHERE type = 'bogus'")  # the documented fix
    conn.commit()
    apply_sql(conn, MIGRATION_003)
    apply_sql(conn, MIGRATION_003)  # idempotent
    cur.execute("SELECT COUNT(*) FROM pg_constraint WHERE conname = 'chk_notifications_type'")
    assert cur.fetchone() == (1,)
