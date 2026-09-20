"""Read-only data-quality checks for the TrendCast database.

Every check is a SELECT that returns the offending rows; a check passes when
it returns none. The session is forced read-only, so this can be pointed at
the live Supabase database safely.

Errors are integrity violations that should never exist. Warnings are
data-quality signals worth a look (drift, staleness, gaps) but can occur
legitimately.

Usage (from backend/):
    python -m tools.data_quality                    # uses SUPABASE_DB_URL
    python -m tools.data_quality --db-url postgresql://...
    python -m tools.data_quality --stale-days 3 --max-missing-pct 5 --strict
    python -m tools.data_quality --json

Exit status: 0 = no errors (warnings allowed unless --strict), 1 = failing
checks, 2 = could not run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg2

ERROR, WARN = "error", "warn"
SAMPLE_SIZE = 5
# Snapshots may be stamped a little ahead of / behind the database clock.
CLOCK_SKEW = "5 minutes"

BACKEND_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


@dataclass(frozen=True)
class Check:
    name: str
    severity: str
    description: str
    sql: str  # SELECT returning the offending rows; may use %(stale_days)s / %(max_missing_pct)s


@dataclass(frozen=True)
class Result:
    check: Check
    count: int
    sample: list

    @property
    def passed(self) -> bool:
        return self.count == 0


CHECKS: list[Check] = [
    # --- referential integrity (the foreign keys should make all of these zero)
    Check(
        "orphan_videos", ERROR, "videos whose channel is missing from channel_stats",
        """SELECT v.video_id, v.channel_id FROM videos v
           LEFT JOIN channel_stats c ON c.channel_id = v.channel_id
           WHERE c.channel_id IS NULL""",
    ),
    Check(
        "orphan_view_timeseries", ERROR, "snapshots whose video is missing from videos",
        """SELECT t.id, t.video_id FROM view_timeseries t
           LEFT JOIN videos v ON v.video_id = t.video_id
           WHERE v.video_id IS NULL""",
    ),
    Check(
        "orphan_video_features", ERROR, "embeddings whose video is missing from videos",
        """SELECT f.video_id FROM video_features f
           LEFT JOIN videos v ON v.video_id = f.video_id
           WHERE v.video_id IS NULL""",
    ),
    Check(
        "orphan_predictions", ERROR, "predictions whose user is missing from users",
        """SELECT p.id, p.user_id FROM predictions p
           LEFT JOIN users u ON u.id = p.user_id
           WHERE u.id IS NULL""",
    ),
    Check(
        "orphan_notifications", ERROR, "notifications whose user is missing from users",
        """SELECT n.id, n.user_id FROM notifications n
           LEFT JOIN users u ON u.id = n.user_id
           WHERE u.id IS NULL""",
    ),
    # --- time series integrity
    Check(
        "duplicate_snapshots", ERROR, "more than one snapshot for the same video at the same instant",
        """SELECT video_id, scraped_at, COUNT(*) AS copies FROM view_timeseries
           GROUP BY video_id, scraped_at HAVING COUNT(*) > 1""",
    ),
    Check(
        "future_snapshots", ERROR, f"snapshots stamped more than {CLOCK_SKEW} in the future",
        f"""SELECT id, video_id, scraped_at FROM view_timeseries
            WHERE scraped_at > NOW() + INTERVAL '{CLOCK_SKEW}'""",
    ),
    Check(
        "future_publish_dates", ERROR, "videos or channels published in the future",
        f"""SELECT 'video' AS kind, video_id AS id, published_at FROM videos
            WHERE published_at > NOW() + INTERVAL '{CLOCK_SKEW}'
            UNION ALL
            SELECT 'channel', channel_id, published_at FROM channel_stats
            WHERE published_at > NOW() + INTERVAL '{CLOCK_SKEW}'""",
    ),
    Check(
        "decreasing_view_counts", WARN, "a snapshot with fewer views than the previous one (counts are cumulative)",
        """SELECT video_id, scraped_at, previous_views, view_count FROM (
               SELECT video_id, scraped_at, view_count,
                      LAG(view_count) OVER (PARTITION BY video_id ORDER BY scraped_at, id) AS previous_views
               FROM view_timeseries
           ) s WHERE view_count < previous_views""",
    ),
    Check(
        "snapshot_before_publish", WARN, "snapshots taken more than an hour before the video's publish time",
        """SELECT t.video_id, t.scraped_at, v.published_at FROM view_timeseries t
           JOIN videos v ON v.video_id = t.video_id
           WHERE t.scraped_at < v.published_at - INTERVAL '1 hour'""",
    ),
    # --- freshness and completeness
    Check(
        "stale_channels", WARN, "channels not refreshed within --stale-days",
        """SELECT channel_id, processed_at FROM channel_stats
           WHERE processed_at < NOW() - make_interval(days => %(stale_days)s)""",
    ),
    Check(
        "incomplete_video_metadata", WARN, "metadata fields missing on more than --max-missing-pct of videos",
        """SELECT field, missing, total FROM (
               SELECT 'title' AS field, COUNT(*) FILTER (WHERE title IS NULL) AS missing, COUNT(*) AS total FROM videos
               UNION ALL
               SELECT 'thumbnail_url', COUNT(*) FILTER (WHERE thumbnail_url IS NULL), COUNT(*) FROM videos
               UNION ALL
               SELECT 'category_id', COUNT(*) FILTER (WHERE category_id IS NULL), COUNT(*) FROM videos
               UNION ALL
               SELECT 'embeddings', COUNT(*) FILTER (WHERE f.video_id IS NULL), COUNT(*)
               FROM videos v LEFT JOIN video_features f ON f.video_id = v.video_id
           ) s WHERE total > 0 AND missing * 100.0 / total > %(max_missing_pct)s""",
    ),
    # --- app data consistency
    Check(
        "complete_predictions_missing_outputs", ERROR, "'complete' predictions without model outputs",
        """SELECT id, user_id FROM predictions
           WHERE status = 'complete'
             AND (predicted_views IS NULL OR trajectory IS NULL OR v_inf IS NULL OR tau IS NULL)""",
    ),
    Check(
        "draft_predictions_with_outputs", ERROR, "'draft' predictions that carry model outputs",
        """SELECT id, user_id FROM predictions
           WHERE status = 'draft' AND (predicted_views IS NOT NULL OR trajectory IS NOT NULL)""",
    ),
    Check(
        "duplicate_emails_case_insensitive", ERROR, "accounts whose emails differ only by letter case",
        """SELECT LOWER(email) AS email, COUNT(*) AS accounts FROM users
           GROUP BY LOWER(email) HAVING COUNT(*) > 1""",
    ),
    Check(
        "users_channel_never_fetched", WARN, "users with a channel URL but neither channel data nor a fetch error",
        """SELECT id, channel_url FROM users
           WHERE channel_url IS NOT NULL AND channel_data IS NULL AND channel_fetch_error IS NULL""",
    ),
]

METRIC_TABLES = [
    "channel_stats", "videos", "view_timeseries", "video_features",
    "users", "predictions", "notifications",
]


def connect_readonly(db_url: str):
    """Opens a connection that cannot write, whatever the SQL says."""
    conn = psycopg2.connect(db_url, options="-c default_transaction_read_only=on")
    conn.set_session(readonly=True, autocommit=True)
    return conn


def run_checks(cur, stale_days: int = 7, max_missing_pct: float = 10.0) -> list[Result]:
    params = {"stale_days": stale_days, "max_missing_pct": max_missing_pct}
    results = []
    for check in CHECKS:
        cur.execute(f"SELECT COUNT(*) FROM ({check.sql}) offending", params)
        count = cur.fetchone()[0]
        sample = []
        if count:
            cur.execute(f"{check.sql} LIMIT {SAMPLE_SIZE}", params)
            sample = cur.fetchall()
        results.append(Result(check, count, sample))
    return results


def collect_metrics(cur, min_snapshots: int = 10) -> dict:
    metrics = {}
    for table in METRIC_TABLES:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        metrics[f"rows.{table}"] = cur.fetchone()[0]
    cur.execute(
        """SELECT COUNT(*) FROM (
               SELECT video_id FROM view_timeseries GROUP BY video_id HAVING COUNT(*) >= %s
           ) s""",
        (min_snapshots,),
    )
    metrics[f"videos_with_at_least_{min_snapshots}_snapshots"] = cur.fetchone()[0]
    cur.execute("SELECT MIN(scraped_at), MAX(scraped_at) FROM view_timeseries")
    oldest, newest = cur.fetchone()
    metrics["snapshots.oldest"] = oldest.isoformat() if oldest else None
    metrics["snapshots.newest"] = newest.isoformat() if newest else None
    return metrics


def exit_code(results: list[Result], strict: bool = False) -> int:
    failing = [r for r in results if not r.passed]
    if any(r.check.severity == ERROR for r in failing):
        return 1
    return 1 if strict and failing else 0


def format_report(results: list[Result], metrics: dict) -> str:
    lines = ["Data quality report", "=" * 60]
    for result in results:
        status = "PASS" if result.passed else ("FAIL" if result.check.severity == ERROR else "WARN")
        lines.append(f"[{status}] {result.check.name}: {result.count}  ({result.check.description})")
        for row in result.sample:
            lines.append(f"         {row}")
        if result.count > len(result.sample):
            lines.append(f"         ... and {result.count - len(result.sample)} more")
    lines += ["", "Metrics", "-" * 60]
    lines += [f"{key}: {value}" for key, value in metrics.items()]
    errors = sum(1 for r in results if not r.passed and r.check.severity == ERROR)
    warnings = sum(1 for r in results if not r.passed and r.check.severity == WARN)
    lines += ["", f"{len(results)} checks: {errors} failing, {warnings} warning(s)"]
    return "\n".join(lines)


def _resolve_db_url(cli_value: str | None) -> str | None:
    if cli_value:
        return cli_value
    if not os.environ.get("SUPABASE_DB_URL") and BACKEND_ENV_PATH.exists():
        from dotenv import load_dotenv

        load_dotenv(BACKEND_ENV_PATH)
    return os.environ.get("SUPABASE_DB_URL")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only data-quality checks.")
    parser.add_argument("--db-url", help="Postgres URL (default: SUPABASE_DB_URL)")
    parser.add_argument("--stale-days", type=int, default=7)
    parser.add_argument("--max-missing-pct", type=float, default=10.0)
    parser.add_argument("--min-snapshots", type=int, default=10)
    parser.add_argument("--strict", action="store_true", help="warnings also fail the run")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    db_url = _resolve_db_url(args.db_url)
    if not db_url:
        print("No database URL: pass --db-url or set SUPABASE_DB_URL", file=sys.stderr)
        return 2

    try:
        conn = connect_readonly(db_url)
    except psycopg2.Error as exc:
        print(f"Could not connect: {exc}", file=sys.stderr)
        return 2
    try:
        with conn.cursor() as cur:
            results = run_checks(cur, args.stale_days, args.max_missing_pct)
            metrics = collect_metrics(cur, args.min_snapshots)
    finally:
        conn.close()

    if args.json:
        print(json.dumps({
            "checks": [
                {"name": r.check.name, "severity": r.check.severity, "count": r.count,
                 "passed": r.passed, "description": r.check.description,
                 "sample": [[str(v) for v in row] for row in r.sample]}
                for r in results
            ],
            "metrics": metrics,
        }, indent=2))
    else:
        print(format_report(results, metrics))
    return exit_code(results, args.strict)


if __name__ == "__main__":
    sys.exit(main())
