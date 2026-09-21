"""Shared fixtures for the database integrity tests.

A throwaway Postgres (pgvector image) is started in Docker once per session.
The schema scripts are applied to a template database, and each test gets its
own copy via CREATE DATABASE ... TEMPLATE, so tests are isolated and fast.

These tests never read the real SUPABASE_DB_URL: the `backend` fixture sets it to
the disposable server before importing config.py and asserts that it took effect,
so they cannot touch the live database. To use an already-running Postgres instead of
Docker, set TEST_DB_ADMIN_URL (a superuser URL for a *disposable* server).
"""

import os
import subprocess
import sys
import time
import types
import uuid
from pathlib import Path
from types import SimpleNamespace

import psycopg2
import pytest
from psycopg2 import sql
from psycopg2.extensions import make_dsn, parse_dsn
from psycopg2.pool import ThreadedConnectionPool

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
SCHEMA_DIR = BACKEND_DIR / "schema"

# Applied in order, exactly as they would be on a fresh database.
INIT_SCRIPTS = sorted((SCHEMA_DIR / "init").glob("0*.sql"))
MIGRATION_002 = SCHEMA_DIR / "migrations" / "002_add_video_metadata.sql"
MIGRATION_003 = SCHEMA_DIR / "migrations" / "003_notifications_type_check.sql"
MIGRATION_004 = SCHEMA_DIR / "migrations" / "004_enable_row_level_security.sql"

# Importable helpers that live in backend/ (tools.*); config.py is only imported by the `backend` fixture.
sys.path.insert(0, str(BACKEND_DIR))

# Name of the Docker container running the disposable server (None when using TEST_DB_ADMIN_URL).
CONTAINER = {"name": None}

PG_IMAGE = os.environ.get("TEST_DB_IMAGE", "pgvector/pgvector:pg16")
TEMPLATE_DB = "tc_template"


def apply_sql(conn, path: Path) -> None:
    with conn.cursor() as cur:
        cur.execute(path.read_text(encoding="utf-8"))
    conn.commit()


def _wait_for_postgres(kwargs: dict, timeout: int = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            psycopg2.connect(**kwargs).close()
            return
        except psycopg2.OperationalError:
            time.sleep(1)
    raise RuntimeError("Test Postgres did not become ready in time")


@pytest.fixture(scope="session")
def pg_server():
    """Connection kwargs (to the 'postgres' db) for a disposable server."""
    external = os.environ.get("TEST_DB_ADMIN_URL")
    if external:
        yield {**parse_dsn(external), "dbname": "postgres"}
        return

    try:
        subprocess.run(["docker", "info"], check=True, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("Docker is not available and TEST_DB_ADMIN_URL is not set")

    name = f"trendcast-test-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name,
         "-e", "POSTGRES_PASSWORD=test", "-p", "127.0.0.1::5432", PG_IMAGE],
        check=True, capture_output=True,
    )
    CONTAINER["name"] = name
    try:
        mapping = subprocess.run(
            ["docker", "port", name, "5432/tcp"],
            check=True, capture_output=True, text=True,
        ).stdout.splitlines()[0]
        kwargs = {
            "host": "127.0.0.1",
            "port": int(mapping.rsplit(":", 1)[1]),
            "user": "postgres",
            "password": "test",
            "dbname": "postgres",
        }
        _wait_for_postgres(kwargs)
        yield kwargs
    finally:
        CONTAINER["name"] = None
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def _admin(pg_server):
    conn = psycopg2.connect(**pg_server)
    conn.autocommit = True
    return conn


@pytest.fixture(scope="session")
def template_db(pg_server):
    """Database with 01..04 applied once; per-test databases are copies of it."""
    admin = _admin(pg_server)
    with admin.cursor() as cur:
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(TEMPLATE_DB)))
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEMPLATE_DB)))
    conn = psycopg2.connect(**{**pg_server, "dbname": TEMPLATE_DB})
    try:
        for script in INIT_SCRIPTS:
            apply_sql(conn, script)
    finally:
        conn.close()
    yield TEMPLATE_DB
    admin.close()


@pytest.fixture
def create_database(pg_server):
    """Factory returning a fresh connection to a new database.

    from_template=True (default): the schema is already applied.
    from_template=False: an empty database, for tests that build the schema.
    """
    admin = _admin(pg_server)
    created, conns = [], []

    def _create(from_template: bool = True):
        name = f"tc_{uuid.uuid4().hex[:12]}"
        stmt = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
        if from_template:
            stmt = sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(name), sql.Identifier(TEMPLATE_DB)
            )
        with admin.cursor() as cur:
            cur.execute(stmt)
        created.append(name)
        conn = psycopg2.connect(**{**pg_server, "dbname": name})
        conns.append(conn)
        return conn

    yield _create

    for conn in conns:
        conn.close()
    with admin.cursor() as cur:
        for name in created:
            cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
    admin.close()


@pytest.fixture
def conn(template_db, create_database):
    """Connection to a fresh per-test copy of the fully migrated schema."""
    connection = create_database()
    yield connection
    connection.rollback()


@pytest.fixture
def cur(conn):
    with conn.cursor() as cursor:
        yield cursor


# ---------------------------------------------------------------------------
# API harness: the real FastAPI routers on top of the disposable database
# ---------------------------------------------------------------------------

class FakeInferenceError(Exception):
    pass


def _stub_inference_module():
    """Stands in for backend/inference.py so tests don't load torch or the model."""
    stub = types.ModuleType("inference")

    def _unexpected(*args, **kwargs):
        raise AssertionError("the model was called but the test did not expect it")

    stub.ChannelNotFoundError = type("ChannelNotFoundError", (Exception,), {})
    stub.InsufficientHistoryError = type("InsufficientHistoryError", (Exception,), {})
    stub.QuotaExceededError = type("QuotaExceededError", (Exception,), {})
    stub.ThumbnailDownloadError = type("ThumbnailDownloadError", (RuntimeError,), {})
    stub.get_state = lambda: object()
    stub.load_artifacts = lambda: None
    stub.run_forecast = lambda *args, **kwargs: _unexpected()

    stub.run_forecast_on_image = _unexpected
    return stub


@pytest.fixture(scope="session")
def backend(pg_server):
    """Imports the backend with config pointed at the disposable server."""
    dsn = make_dsn(**pg_server)
    overrides = {"SUPABASE_DB_URL": dsn, "JWT_SECRET_KEY": "test-secret", "YOUTUBE_API_KEY": "test-key"}
    saved_env = {k: os.environ.get(k) for k in overrides}
    saved_modules = {k: sys.modules.get(k) for k in ("inference",)}
    os.environ.update(overrides)
    sys.modules["inference"] = _stub_inference_module()

    import config
    import db
    from routers import auth, channel, dashboard, notifications, predictions, trends
    import storage

    # A real .env must never win over the disposable server.
    assert config.SUPABASE_DB_URL == dsn, "backend is not pointed at the disposable test database"

    from fastapi import FastAPI

    app = FastAPI()
    for module in (auth, channel, dashboard, notifications, predictions, trends):
        app.include_router(module.router)

    yield SimpleNamespace(
        app=app, db=db, storage=storage, inference=sys.modules["inference"],
        channel_router=channel, predictions_router=predictions,
    )

    db.pool.closeall()
    for key, value in saved_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    for key, value in saved_modules.items():
        if value is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = value


@pytest.fixture
def api(backend, pg_server, template_db, create_database, monkeypatch, tmp_path):
    """TestClient plus a direct DB cursor, backed by a fresh per-test database."""
    from fastapi.testclient import TestClient

    verify_conn = create_database()
    pool = ThreadedConnectionPool(1, backend.db.MAX_CONNECTIONS, **{**pg_server, "dbname": verify_conn.info.dbname})
    monkeypatch.setattr(backend.db, "pool", pool)
    monkeypatch.setattr(backend.storage, "UPLOADS_DIR", tmp_path)

    client = TestClient(backend.app, raise_server_exceptions=False)
    with verify_conn.cursor() as cursor:
        yield SimpleNamespace(
            client=client, conn=verify_conn, cur=cursor, uploads=tmp_path,
            backend=backend, monkeypatch=monkeypatch,
        )
    pool.closeall()


@pytest.fixture
def db_dsn(pg_server):
    """Returns a function mapping a connection to a full DSN (with password) for its database."""
    return lambda connection: make_dsn(**{**pg_server, "dbname": connection.info.dbname})
