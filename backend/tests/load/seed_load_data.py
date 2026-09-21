"""Seeds (and removes) the data the load test runs against.

    python seed_load_data.py seed    [--users 200] [--channels 200] ...
    python seed_load_data.py cleanup
    python seed_load_data.py stats

Only LOAD_TEST_DB_URL is used (see load_config.py for the safety checks).
Every row created is marked (email loadtest_*@example.com, channel UCLOAD*), and
cleanup deletes only those rows, so it is safe to run against a database that
holds other data too.

Seeded:
  users              N accounts sharing one bcrypt hash of load_config.PASSWORD
                     (bcrypt at the backend's own cost, so /auth/login costs what it will in production)
  notifications      per user
  predictions        per user (mix of drafts and completed, with a trajectory JSON)
  channel_stats      static pipeline data that /channels reads
  videos             per channel
  view_timeseries    dense series for the first K videos of each channel
The ETL is never run; this is static data written directly.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

import bcrypt
import psycopg2

import load_config as lc

SCALES = {
    # users, channels, videos/channel, timeseries videos/channel, points/series, predictions/user, notifications/user
    "small": (50, 50, 20, 2, 100, 5, 5),
    "medium": (200, 200, 30, 3, 150, 10, 10),
    "large": (1000, 1000, 40, 4, 300, 25, 20),
}


def connect(url: str):
    return psycopg2.connect(url, connect_timeout=15)


def schema_present(cur) -> bool:
    cur.execute("SELECT to_regclass('public.users') IS NOT NULL AND to_regclass('public.view_timeseries') IS NOT NULL")
    return cur.fetchone()[0]


def apply_schema(conn) -> None:
    for script in sorted(lc.SCHEMA_INIT_DIR.glob("0*.sql")):
        print(f"  applying {script.name}")
        with conn.cursor() as cur:
            cur.execute(script.read_text(encoding="utf-8"))
        conn.commit()


def cleanup(conn) -> dict:
    """Deletes only rows carrying the load-test markers. Cascades remove dependents."""
    removed = {}
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM users WHERE email LIKE %s OR email LIKE %s",
            (lc.SEED_EMAIL_PATTERN, lc.SIGNUP_EMAIL_PATTERN),
        )
        removed["users"] = cur.rowcount
        cur.execute("DELETE FROM channel_stats WHERE channel_id LIKE %s", (lc.SEED_CHANNEL_PATTERN,))
        removed["channels"] = cur.rowcount
    conn.commit()
    return removed


def seed(conn, users, channels, vids, ts_vids, points, preds, notifs) -> dict:
    password_hash = bcrypt.hashpw(lc.PASSWORD.encode(), bcrypt.gensalt()).decode()  # default cost 12, like security.py

    with conn.cursor() as cur:
        # ---- pipeline data (static; what /channels and /videos read) ------------------------------
        cur.execute(
            """
            INSERT INTO channel_stats
                (channel_id, channel_title, channel_description, published_at, country,
                 total_views, subscriber_count, video_count, processed_at)
            SELECT 'UCLOAD' || lpad(g::text, 18, '0'),
                   'Load Channel ' || g,
                   'Seeded for load testing. ' || repeat('lorem ipsum ', 20),
                   now() - (random() * 3000 + 100) * interval '1 day',
                   (ARRAY['US','IN','GB','LK','DE','BR','JP'])[1 + (g %% 7)],
                   (subs * (5 + random() * 200))::bigint,
                   subs::bigint,
                   %(vids)s,
                   now()
            FROM (SELECT g, floor(power(10, 2 + random() * 5.5)) AS subs
                  FROM generate_series(1, %(channels)s) g) t
            """,
            {"channels": channels, "vids": vids},
        )
        cur.execute(
            """
            INSERT INTO videos (video_id, channel_id, published_at, status, last_polled_at,
                                next_poll_at, current_interval_hours, title, category_id)
            SELECT 'LD' || lpad(c::text, 5, '0') || lpad(v::text, 4, '0'),
                   'UCLOAD' || lpad(c::text, 18, '0'),
                   now() - v * interval '2 days' - random() * interval '1 day',
                   'active', now(), now() + interval '1 hour', 1,
                   'Load video ' || c || '-' || v,
                   (ARRAY['10','22','24','27'])[1 + (v %% 4)]
            FROM generate_series(1, %(channels)s) c, generate_series(1, %(vids)s) v
            """,
            {"channels": channels, "vids": vids},
        )
        cur.execute(
            """
            INSERT INTO view_timeseries (video_id, scraped_at, view_count, like_count, comment_count)
            SELECT v.video_id,
                   v.published_at + p * interval '30 minutes',
                   1000 + p * 40 + (p * p) / 3,
                   50 + p * 2,
                   5 + p / 4
            FROM videos v, generate_series(0, %(points)s - 1) p
            WHERE v.channel_id LIKE %(chan_pat)s
              AND substr(v.video_id, 8)::int <= %(ts_vids)s
            """,
            {"points": points, "ts_vids": ts_vids, "chan_pat": lc.SEED_CHANNEL_PATTERN},
        )

        # ---- app data ------------------------------------------------------------------------------
        cur.execute(
            """
            INSERT INTO users (full_name, email, password_hash, subscribers, monthly_views,
                               channel_url, channel_data)
            SELECT 'Load Test User ' || g,
                   'loadtest_' || lpad(g::text, 5, '0') || '@example.com',
                   %(hash)s,
                   (1000 + random() * 500000)::bigint,
                   (10000 + random() * 5000000)::bigint,
                   'https://youtube.com/@loaduser' || g,
                   jsonb_build_object(
                       'channel_id', 'UCLOADUSER' || lpad(g::text, 14, '0'),
                       'title', 'Load User Channel ' || g,
                       'thumbnail_url', 'https://img.example/t.jpg',
                       'subscriber_count', 1000 + g,
                       'view_count', 100000 + g,
                       'video_count', 50,
                       'subscriber_hidden', false,
                       'fetched_at', now())
            FROM generate_series(1, %(users)s) g
            """,
            {"hash": password_hash, "users": users},
        )
        cur.execute(
            """
            INSERT INTO notifications (user_id, type, title, message, read)
            SELECT u.id,
                   (ARRAY['welcome','channel_fetch_success','channel_fetch_error','prediction_complete'])[1 + (n %% 4)],
                   'Seeded notification ' || n,
                   'Seeded for load testing.',
                   n %% 3 = 0
            FROM users u, generate_series(1, %(notifs)s) n
            WHERE u.email LIKE %(pat)s
            """,
            {"notifs": notifs, "pat": lc.SEED_EMAIL_PATTERN},
        )
        cur.execute(
            """
            INSERT INTO predictions (user_id, title, category, tags, target_date, status, predicted_views,
                                     confidence, change_vs_avg, trajectory, v_inf, tau, used_channel_context)
            SELECT u.id,
                   'Seeded prediction ' || n,
                   (ARRAY['Gaming','Music','Education','Vlog','Tech'])[1 + (n %% 5)],
                   ARRAY['load','seed'],
                   current_date + n,
                   CASE WHEN n %% 4 = 0 THEN 'draft' ELSE 'complete' END,
                   CASE WHEN n %% 4 = 0 THEN NULL ELSE 5000 + n * 1000 END,
                   CASE WHEN n %% 4 = 0 THEN NULL ELSE 0.6 END,
                   CASE WHEN n %% 4 = 0 THEN NULL ELSE 0.12 END,
                   CASE WHEN n %% 4 = 0 THEN NULL ELSE
                        (SELECT jsonb_agg(jsonb_build_object('day', d, 'views', d * 1000)) FROM generate_series(1, 7) d)
                   END,
                   CASE WHEN n %% 4 = 0 THEN NULL ELSE 10000.0 END,
                   CASE WHEN n %% 4 = 0 THEN NULL ELSE 2.5 END,
                   CASE WHEN n %% 4 = 0 THEN NULL ELSE true END
            FROM users u, generate_series(1, %(preds)s) n
            WHERE u.email LIKE %(pat)s
            """,
            {"preds": preds, "pat": lc.SEED_EMAIL_PATTERN},
        )
        cur.execute("SELECT id, email FROM users WHERE email LIKE %s ORDER BY id", (lc.SEED_EMAIL_PATTERN,))
        user_rows = [{"id": i, "email": e} for i, e in cur.fetchall()]
        cur.execute(
            "SELECT channel_id FROM channel_stats WHERE channel_id LIKE %s ORDER BY channel_id",
            (lc.SEED_CHANNEL_PATTERN,),
        )
        channel_ids = [r[0] for r in cur.fetchall()]
    conn.commit()

    channel_entries = []
    for n, cid in enumerate(channel_ids, start=1):
        channel_entries.append(
            {
                "channel_id": cid,
                "videos": [f"LD{n:05d}{v:04d}" for v in range(1, vids + 1)],
                "series_videos": [f"LD{n:05d}{v:04d}" for v in range(1, min(ts_vids, vids) + 1)],
            }
        )
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "password": lc.PASSWORD,
        "users": user_rows,
        "channels": channel_entries,
        "points_per_series": points,
    }
    lc.MANIFEST_PATH.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def stats(conn) -> None:
    queries = {
        "seeded users": ("SELECT count(*) FROM users WHERE email LIKE %s", (lc.SEED_EMAIL_PATTERN,)),
        "signup users (from test runs)": ("SELECT count(*) FROM users WHERE email LIKE %s", (lc.SIGNUP_EMAIL_PATTERN,)),
        "seeded channels": ("SELECT count(*) FROM channel_stats WHERE channel_id LIKE %s", (lc.SEED_CHANNEL_PATTERN,)),
        "seeded videos": ("SELECT count(*) FROM videos WHERE channel_id LIKE %s", (lc.SEED_CHANNEL_PATTERN,)),
        "seeded timeseries rows": (
            "SELECT count(*) FROM view_timeseries t JOIN videos v USING (video_id) WHERE v.channel_id LIKE %s",
            (lc.SEED_CHANNEL_PATTERN,),
        ),
        "predictions (load users)": (
            "SELECT count(*) FROM predictions p JOIN users u ON u.id = p.user_id "
            "WHERE u.email LIKE %s OR u.email LIKE %s",
            (lc.SEED_EMAIL_PATTERN, lc.SIGNUP_EMAIL_PATTERN),
        ),
        "notifications (load users)": (
            "SELECT count(*) FROM notifications n JOIN users u ON u.id = n.user_id "
            "WHERE u.email LIKE %s OR u.email LIKE %s",
            (lc.SEED_EMAIL_PATTERN, lc.SIGNUP_EMAIL_PATTERN),
        ),
        "ALL rows in channel_stats": ("SELECT count(*) FROM channel_stats", ()),
    }
    with conn.cursor() as cur:
        for label, (sql, params) in queries.items():
            cur.execute(sql, params)
            print(f"  {label:32s} {cur.fetchone()[0]:>10,}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["seed", "cleanup", "stats"])
    parser.add_argument("--scale", choices=SCALES, default="medium", help="preset sizes (default: medium)")
    parser.add_argument("--users", type=int)
    parser.add_argument("--channels", type=int)
    parser.add_argument("--videos-per-channel", type=int)
    parser.add_argument("--series-videos-per-channel", type=int, help="videos per channel that get a timeseries")
    parser.add_argument("--points", type=int, help="timeseries rows per series")
    parser.add_argument("--predictions-per-user", type=int)
    parser.add_argument("--notifications-per-user", type=int)
    parser.add_argument("--allow-remote", action="store_true", help="permit a non-local dedicated load-test database")
    parser.add_argument("--apply-schema", action="store_true", help="apply backend/schema/init/*.sql if the schema is missing")
    args = parser.parse_args()

    url = lc.require_test_db_url(allow_remote=args.allow_remote)
    print(f"Target database: {lc.describe(url)}")
    conn = connect(url)
    try:
        with conn.cursor() as cur:
            present = schema_present(cur)
        if not present:
            if args.apply_schema:
                print("Schema missing; applying backend/schema/init/*.sql")
                apply_schema(conn)
            else:
                sys.exit("The target database has no TrendCast schema. Re-run with --apply-schema, or apply backend/schema/init yourself.")

        try:  # lets monitor.py report the most expensive queries; harmless if unavailable
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
            conn.commit()
        except psycopg2.Error:
            conn.rollback()

        if args.command == "stats":
            stats(conn)
            return
        if args.command == "cleanup":
            print("Removed:", cleanup(conn))
            lc.MANIFEST_PATH.unlink(missing_ok=True)
            return

        sizes = list(SCALES[args.scale])
        overrides = [args.users, args.channels, args.videos_per_channel, args.series_videos_per_channel,
                     args.points, args.predictions_per_user, args.notifications_per_user]
        sizes = [o if o is not None else s for o, s in zip(overrides, sizes)]
        users, channels, vids, ts_vids, points, preds, notifs = sizes
        if channels > 99999 or vids > 9999:
            sys.exit("--channels must be <= 99999 and --videos-per-channel <= 9999 (id format).")

        print(f"Removing any previous load-test rows, then seeding {users} users, {channels} channels x {vids} videos, "
              f"{ts_vids * channels} series x {points} points ...")
        cleanup(conn)
        manifest = seed(conn, users, channels, vids, ts_vids, points, preds, notifs)
        print(f"Done. Manifest written to {lc.MANIFEST_PATH} ({len(manifest['users'])} users, {len(manifest['channels'])} channels).")
        stats(conn)
        with conn.cursor() as cur:
            cur.execute("ANALYZE")  # planner statistics for the freshly loaded tables
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
