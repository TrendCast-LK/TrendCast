"""Turns a Locust run (and optionally the monitor CSV) into a Markdown report with pass/fail verdicts.

    python analyze_results.py results/spike/spike            # prefix given to `locust --csv`
    python analyze_results.py results/ramp/ramp --monitor results/ramp/monitor.csv --out results/ramp/report.md

Reads <prefix>_stats.csv and <prefix>_stats_history.csv. The thresholds are the ones in
load_config.py (override with the LOAD_*_MS / LOAD_MAX_ERROR_PCT environment variables).
Exit code 1 if any criterion fails, so it can gate a CI job.

For step/ramp runs it also reports the "knee": the highest user count at which the aggregate
p95 (held to the read p99 budget, because the aggregate includes slower endpoints) and the
error rate were still inside the limits.
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path

import load_config as lc


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def num(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default  # Locust writes "N/A" for empty percentiles


def endpoint_rows(stats: list[dict]) -> tuple[list[dict], dict]:
    rows, total = [], {}
    for r in stats:
        row = {
            "name": r["Name"],
            "requests": int(num(r["Request Count"])),
            "failures": int(num(r["Failure Count"])),
            "median": num(r["Median Response Time"]),
            "avg": num(r["Average Response Time"]),
            "p95": num(r["95%"]),
            "p99": num(r["99%"]),
            "max": num(r["Max Response Time"]),
            "rps": num(r["Requests/s"]),
        }
        if r["Name"] == "Aggregated":
            total = row
        else:
            rows.append(row)
    return rows, total


def knee(history: list[dict], thresholds: dict) -> tuple[int | None, list[tuple[int, float, float, float]]]:
    """Per user-count level: aggregate p95 (worst of the level), failure share, and requests/s (median)."""
    levels: dict[int, list[dict]] = {}
    for r in history:
        if r["Name"] == "Aggregated" and num(r["User Count"]) > 0:
            levels.setdefault(int(num(r["User Count"])), []).append(r)
    summary = []
    for users in sorted(levels):
        samples = levels[users]
        p95 = max(num(s["95%"]) for s in samples)
        rps = statistics.median(num(s["Requests/s"]) for s in samples)
        fps = statistics.median(num(s["Failures/s"]) for s in samples)
        fail_pct = 100.0 * fps / rps if rps else 0.0
        summary.append((users, p95, fail_pct, rps))
    # the aggregate mixes slower endpoints (auth, model) into the reads, so its p95 is held to the read p99 budget
    ok = [u for u, p95, fail, _ in summary if p95 <= thresholds["read_p99_ms"] and fail <= thresholds["max_error_pct"]]
    return (max(ok) if ok else None), summary


def monitor_summary(path: Path) -> list[str]:
    rows = read_csv(path)
    if not rows:
        return []

    def col(name):
        return [num(r[name]) for r in rows if r.get(name) not in (None, "")]

    lines = ["## Server resources", "", "| Metric | Average | Peak |", "|---|---|---|"]
    for label, name in [
        ("Backend CPU % (100 = one core)", "backend_cpu_pct"),
        ("Backend memory (MB)", "backend_rss_mb"),
        ("Backend threads", "backend_threads"),
        ("System CPU %", "system_cpu_pct"),
        ("System memory %", "system_mem_pct"),
        ("DB connections", "db_connections"),
        ("DB active queries", "db_active"),
        ("DB sessions waiting on locks", "db_lock_waits"),
        ("DB longest query (s)", "db_longest_query_s"),
    ]:
        values = col(name)
        if values:
            lines.append(f"| {label} | {statistics.mean(values):.1f} | {max(values):.1f} |")
    rss = col("backend_rss_mb")
    if len(rss) >= 20:
        head, tail = statistics.mean(rss[: len(rss) // 5]), statistics.mean(rss[-len(rss) // 5:])
        lines += ["", f"Memory drift: first fifth of the run averaged {head:.0f} MB, last fifth {tail:.0f} MB "
                      f"({tail - head:+.0f} MB). A steady climb during a soak run suggests a leak."]
    return lines + [""]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("prefix", help="the --csv prefix passed to locust")
    parser.add_argument("--monitor", help="monitor.csv from monitor.py")
    parser.add_argument("--out", help="write the report here as well as printing it")
    parser.add_argument("--title", default=None)
    args = parser.parse_args()

    prefix = Path(args.prefix)
    stats_path = prefix.with_name(prefix.name + "_stats.csv")
    if not stats_path.exists():
        sys.exit(f"{stats_path} not found (did locust run with --csv {args.prefix}?)")
    rows, total = endpoint_rows(read_csv(stats_path))
    verdicts = lc.evaluate(rows)
    t = lc.THRESHOLDS

    lines = [f"# {args.title or 'Load test: ' + prefix.name}", ""]
    lines += [
        f"Total requests: **{total.get('requests', 0):,}** | failures: **{total.get('failures', 0):,}** "
        f"({100 * total.get('failures', 0) / max(total.get('requests', 1), 1):.2f}%) | "
        f"throughput: **{total.get('rps', 0):.1f} req/s** | median: {total.get('median', 0):.0f} ms | "
        f"p95: {total.get('p95', 0):.0f} ms | p99: {total.get('p99', 0):.0f} ms",
        "",
        "## Criteria",
        "",
        f"Limits: read p95 <= {t['read_p95_ms']:.0f} ms, read p99 <= {t['read_p99_ms']:.0f} ms, "
        f"auth p95 <= {t['auth_p95_ms']:.0f} ms, prediction p95 <= {t['predict_p95_ms']:.0f} ms, "
        f"error rate <= {t['max_error_pct']:.1f}%.",
        "",
        "| Result | Endpoint | Detail |",
        "|---|---|---|",
    ]
    lines += [f"| {'PASS' if v['passed'] else '**FAIL**'} | {v['name']} | {v['detail']} |" for v in verdicts]

    lines += ["", "## Per endpoint", "",
              "| Endpoint | Requests | Failures | Median ms | Avg ms | p95 ms | p99 ms | Max ms | req/s |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in sorted(rows, key=lambda r: -r["requests"]):
        lines.append(f"| {r['name']} | {r['requests']:,} | {r['failures']:,} | {r['median']:.0f} | {r['avg']:.0f} | "
                     f"{r['p95']:.0f} | {r['p99']:.0f} | {r['max']:.0f} | {r['rps']:.1f} |")

    history_path = prefix.with_name(prefix.name + "_stats_history.csv")
    if history_path.exists():
        best, summary = knee(read_csv(history_path), t)
        if len(summary) > 1:
            lines += ["", "## Capacity by user count", "",
                      "| Users | Aggregate p95 ms | Failure % | req/s |", "|---:|---:|---:|---:|"]
            lines += [f"| {u} | {p95:.0f} | {fail:.2f} | {rps:.1f} |" for u, p95, fail, rps in summary]
            lines += ["", f"Highest user count still inside the limits: **{best if best else 'none'}**."]

    if args.monitor and Path(args.monitor).exists():
        lines += [""] + monitor_summary(Path(args.monitor))

    report = "\n".join(lines) + "\n"
    print(report)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
    sys.exit(0 if all(v["passed"] for v in verdicts) else 1)


if __name__ == "__main__":
    main()
