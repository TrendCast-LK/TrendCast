"""psycopg2 connection pool for the Supabase/Postgres database.

Same connection target (SUPABASE_DB_URL) and driver as the ETL jobs in
youtube-etl-pipeline/youtube_extractor/job2_timeseries_collector.py, wrapped
in a pool since the API serves concurrent requests instead of a single
one-shot run.

FastAPI runs sync endpoints on a thread pool, so the pool must be thread-safe
(ThreadedConnectionPool). It raises immediately when all connections are in
use instead of waiting, so a semaphore makes excess requests queue for a free
connection instead of failing.
"""

import threading
from contextlib import contextmanager

import psycopg2
from psycopg2.pool import ThreadedConnectionPool

from config import SUPABASE_DB_URL

MAX_CONNECTIONS = 10
CONNECTION_WAIT_SECONDS = 30

pool = ThreadedConnectionPool(minconn=1, maxconn=MAX_CONNECTIONS, dsn=SUPABASE_DB_URL)
_slots = threading.BoundedSemaphore(MAX_CONNECTIONS)


def _init_connection(conn):
    """Ensure connection is in write mode and autocommit is off."""
    conn.set_session(readonly=False, autocommit=False)
    return conn


@contextmanager
def get_connection():
    # A request holds at most one connection at a time (never nest these), so
    # waiting here cannot deadlock; the timeout is a backstop, not a budget.
    if not _slots.acquire(timeout=CONNECTION_WAIT_SECONDS):
        raise RuntimeError("timed out waiting for a free database connection")
    try:
        conn = pool.getconn()
        try:
            _init_connection(conn)
            yield conn
        finally:
            pool.putconn(conn)
    finally:
        _slots.release()


@contextmanager
def get_cursor(commit: bool = False):
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            yield cur
            if commit:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
