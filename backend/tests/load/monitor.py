"""Samples server-side resource use while a load test runs.

    python monitor.py --port 8100 --out results/run1/monitor.csv [--duration 600]

Once a second (--interval) it records, into a CSV:
  * the backend process tree (found from the port it listens on, or --pid): CPU %, memory, threads
  * the whole machine: CPU %, memory %
  * the load-test database (pg_stat_activity): connections total / active / idle / idle-in-transaction,
    sessions waiting on a lock, and the longest running query

The API's pool holds at most 10 connections (db.py MAX_CONNECTIONS). `db_active` pinned near 10 while
latency climbs is the signature of pool saturation; CPU near 100 % per core with low db_active means
the bottleneck is Python/bcrypt/model, not the database.

Stop with Ctrl+C (or --duration). When pg_stat_statements is installed it also prints the most expensive
queries of the run (statistics are reset at the start; needs the extension, see docker-compose.load.yml).

Only LOAD_TEST_DB_URL is used; run it on the machine that runs the backend.
"""

from __future__ import annotations

import argparse
import csv
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import psutil
import psycopg2

import load_config as lc

ACTIVITY_SQL = """
    SELECT count(*) FILTER (WHERE pid <> pg_backend_pid()),
           count(*) FILTER (WHERE state = 'active' AND pid <> pg_backend_pid()),
           count(*) FILTER (WHERE state = 'idle' AND pid <> pg_backend_pid()),
           count(*) FILTER (WHERE state LIKE 'idle in transaction%'),
           count(*) FILTER (WHERE wait_event_type = 'Lock'),
           coalesce(max(extract(epoch FROM now() - query_start)) FILTER (WHERE state = 'active' AND pid <> pg_backend_pid()), 0)
    FROM pg_stat_activity
    WHERE datname = current_database()
"""

FIELDS = [
    "timestamp", "elapsed_s", "backend_cpu_pct", "backend_rss_mb", "backend_threads", "backend_procs",
    "system_cpu_pct", "system_mem_pct",
    "db_connections", "db_active", "db_idle", "db_idle_in_tx", "db_lock_waits", "db_longest_query_s",
]


def find_backend(port: int | None, pid: int | None) -> psutil.Process | None:
    if pid:
        return psutil.Process(pid)
    if port is None:
        return None
    for conn in psutil.net_connections(kind="tcp"):
        if conn.status == psutil.CONN_LISTEN and conn.laddr.port == port and conn.pid:
            return psutil.Process(conn.pid)
    return None


def process_tree(root: psutil.Process) -> list[psutil.Process]:
    try:
        return [root, *root.children(recursive=True)]
    except psutil.Error:
        return []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8100, help="port the backend listens on (default 8100)")
    parser.add_argument("--pid", type=int, help="backend PID, instead of looking it up from --port")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--duration", type=float, help="stop after this many seconds")
    parser.add_argument("--out", default=str(lc.RESULTS_DIR / "monitor.csv"))
    parser.add_argument("--stop-file", help="stop (and print the query report) once this file exists")
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--no-db", action="store_true", help="skip the database samples")
    args = parser.parse_args()

    conn = None
    if not args.no_db:
        conn = psycopg2.connect(lc.require_test_db_url(args.allow_remote), connect_timeout=10)
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_stat_statements_reset()")
        except psycopg2.Error:
            pass  # extension not installed: skip the statement report

    backend = find_backend(args.port, args.pid)
    if backend is None:
        print(f"No process is listening on port {args.port}; backend samples will be empty until it starts.", file=sys.stderr)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    stop = False

    def _stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    primed: dict[int, psutil.Process] = {}
    psutil.cpu_percent(None)
    start = time.time()
    print(f"Monitoring -> {out} (Ctrl+C to stop)")
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, FIELDS)
        writer.writeheader()
        while not stop:
            tick = time.time()
            if backend is None or not backend.is_running():
                backend = find_backend(args.port, args.pid)

            cpu = rss = threads = procs = 0.0
            if backend is not None:
                for proc in process_tree(backend):
                    proc = primed.setdefault(proc.pid, proc)  # cpu_percent needs the same object across samples
                    try:
                        cpu += proc.cpu_percent(None)
                        rss += proc.memory_info().rss
                        threads += proc.num_threads()
                        procs += 1
                    except psutil.Error:
                        primed.pop(proc.pid, None)

            row = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "elapsed_s": round(tick - start, 1),
                "backend_cpu_pct": round(cpu, 1),  # 100 = one full core; can exceed 100 on multi-core
                "backend_rss_mb": round(rss / 1048576, 1),
                "backend_threads": int(threads),
                "backend_procs": int(procs),
                "system_cpu_pct": psutil.cpu_percent(None),
                "system_mem_pct": psutil.virtual_memory().percent,
            }
            if conn is not None:
                try:
                    with conn.cursor() as cur:
                        cur.execute(ACTIVITY_SQL)
                        values = cur.fetchone()
                    row.update(zip(FIELDS[8:], [float(v) if k == "db_longest_query_s" else int(v)
                                                 for k, v in zip(FIELDS[8:], values)]))
                except psycopg2.Error as exc:
                    print(f"db sample failed: {exc}", file=sys.stderr)
            writer.writerow(row)
            fh.flush()

            if args.duration and tick - start >= args.duration:
                break
            if args.stop_file and Path(args.stop_file).exists():
                break
            time.sleep(max(0.0, args.interval - (time.time() - tick)))

    if conn is not None:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT calls, round(total_exec_time::numeric, 0), round(mean_exec_time::numeric, 2), "
                    "left(regexp_replace(query, '\\s+', ' ', 'g'), 90) FROM pg_stat_statements "
                    "WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database()) "
                    "ORDER BY total_exec_time DESC LIMIT 10"
                )
                print("\nTop queries by total time (calls | total ms | mean ms | query):")
                for calls, total, mean, query in cur.fetchall():
                    print(f"  {calls:>8} | {total:>9} | {mean:>8} | {query}")
        except psycopg2.Error:
            print("\n(pg_stat_statements not available; skipping the query report)")
        conn.close()
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
