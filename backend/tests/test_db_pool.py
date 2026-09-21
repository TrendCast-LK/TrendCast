"""The backend's connection pool must be safe under FastAPI's worker threads."""

import threading
import time

import pytest
from psycopg2.pool import ThreadedConnectionPool


def test_pool_is_thread_safe_type(api):
    assert isinstance(api.backend.db.pool, ThreadedConnectionPool)


def run_in_threads(target, count):
    results, errors = [None] * count, []

    def worker(index):
        try:
            results[index] = target(index)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results, errors


def test_concurrent_requests_each_get_their_own_connection(api):
    db = api.backend.db

    def query(index):
        with db.get_cursor() as cur:
            cur.execute("SELECT pg_sleep(0.05), %s::int", (index,))
            return cur.fetchone()[1]

    results, errors = run_in_threads(query, count=8)
    assert errors == []
    assert results == list(range(8))  # a shared connection would cross the answers


def test_more_threads_than_connections_wait_instead_of_failing(api):
    db = api.backend.db
    count = db.MAX_CONNECTIONS * 4

    def query(index):
        with db.get_cursor() as cur:
            cur.execute("SELECT pg_sleep(0.05), %s::int", (index,))
            return cur.fetchone()[1]

    results, errors = run_in_threads(query, count=count)
    assert errors == []
    assert results == list(range(count))


def test_concurrent_writes_are_all_committed(api):
    db = api.backend.db

    def insert(index):
        with db.get_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO users (full_name, email, password_hash) VALUES ('U', %s, 'x')",
                (f"user{index}@example.com",),
            )

    _, errors = run_in_threads(insert, count=30)
    assert errors == []
    api.cur.execute("SELECT COUNT(*) FROM users")
    assert api.cur.fetchone() == (30,)


def test_connections_are_returned_after_errors(api):
    db = api.backend.db

    def fail(index):
        with pytest.raises(Exception):
            with db.get_cursor(commit=True) as cur:
                cur.execute("SELECT 1/0")

    # more failures than the pool has connections: each must release its slot
    _, errors = run_in_threads(fail, count=db.MAX_CONNECTIONS * 3)
    assert errors == []
    with db.get_cursor() as cur:
        cur.execute("SELECT 1")
        assert cur.fetchone() == (1,)


def test_a_failed_transaction_does_not_leak_into_the_next_request(api):
    db = api.backend.db
    with pytest.raises(Exception):
        with db.get_cursor(commit=True) as cur:
            cur.execute("INSERT INTO users (full_name, email, password_hash) VALUES ('U', 'a@example.com', 'x')")
            cur.execute("SELECT 1/0")
    with db.get_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM users")
        assert cur.fetchone() == (0,)


def test_waiting_for_a_connection_times_out_instead_of_hanging(api, monkeypatch):
    db = api.backend.db
    monkeypatch.setattr(db, "CONNECTION_WAIT_SECONDS", 0.2)
    held = []
    try:
        for _ in range(db.MAX_CONNECTIONS):
            context = db.get_connection()
            context.__enter__()
            held.append(context)
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="timed out"):
            with db.get_connection():
                pass
        assert time.monotonic() - started < 5
    finally:
        for context in held:
            context.__exit__(None, None, None)
    with db.get_cursor() as cur:  # slots were released
        cur.execute("SELECT 1")
