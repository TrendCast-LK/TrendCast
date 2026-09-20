"""psycopg2 connection pool for the Supabase/Postgres database.

Same connection target (SUPABASE_DB_URL) and driver as the ETL jobs in
youtube-etl-pipeline/youtube_extractor/job2_timeseries_collector.py, wrapped
in a pool since the API serves concurrent requests instead of a single
one-shot run.
"""

from contextlib import contextmanager

import psycopg2
from psycopg2.pool import SimpleConnectionPool

from config import SUPABASE_DB_URL

pool = SimpleConnectionPool(minconn=1, maxconn=10, dsn=SUPABASE_DB_URL)


def _init_connection(conn):
    """Ensure connection is in write mode and autocommit is off."""
    conn.set_session(readonly=False, autocommit=False)
    return conn


@contextmanager
def get_connection():
    conn = pool.getconn()
    try:
        _init_connection(conn)
        yield conn
    finally:
        pool.putconn(conn)


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
