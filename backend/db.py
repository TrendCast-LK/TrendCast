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
# Connections opened at startup (in the background, see warm_pool) so the
# first concurrent requests don't each pay for a new connection.
WARM_CONNECTIONS = 3


class KeepIdlePool(ThreadedConnectionPool):
    """A ThreadedConnectionPool that keeps returned connections open.

    psycopg2 closes any connection handed back while minconn are already idle,
    and opens only minconn at creation. With minconn=1 that meant every request
    overlapping another opened a fresh connection and closed it afterwards;
    against a remote Supabase pooler a new connection can take tens of
    seconds. Opening one connection at creation and then raising minconn to
    maxconn keeps every connection once opened.
    """

    def __init__(self, maxconn: int, *args, **kwargs):
        super().__init__(1, maxconn, *args, **kwargs)
        self.minconn = maxconn


pool = KeepIdlePool(MAX_CONNECTIONS, dsn=SUPABASE_DB_URL)
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
        if conn.closed:  # idle connections are kept now, so one may have been closed meanwhile
            pool.putconn(conn, close=True)
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



def warm_pool(count: int = WARM_CONNECTIONS) -> None:
    """Opens `count` connections in parallel and leaves them idle in the pool.
    Best effort, for a background thread at startup: each thread holds its
    connection until all have one, so they are distinct connections."""
    barrier = threading.Barrier(count, timeout=CONNECTION_WAIT_SECONDS * 4)

    def _open_one():
        try:
            with get_connection():
                barrier.wait()
        except Exception:  # noqa: BLE001 - a failed warm only means a slower first request
            barrier.abort()

    threads = [threading.Thread(target=_open_one, daemon=True) for _ in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
