import { useEffect, useMemo, useRef, useState } from "react";
import Chart from "chart.js/auto";
import AdminLayout, { ErrorBanner, LoadingBlocks, StatTile } from "../../components/admin/AdminLayout";
import { useAdminAuth } from "../../context/AdminAuthContext";
import { useTheme } from "../../context/ThemeContext";
import * as adminApi from "../../lib/adminApi";
import { chartColors, seriesColors } from "../../lib/chartTheme";
import { ENSEMBLE_SINCE, formatCompact } from "../../lib/format";

const ACTIVITY_SERIES = [
  { key: "signups", label: "Signups" },
  { key: "predictions", label: "Predictions created" },
];

const MODEL_SERIES = [
  { key: "ensemble", label: "Ensemble (with channel history)" },
  { key: "catboost_only", label: "CatBoost only" },
];

function percent(part, whole) {
  return whole > 0 ? `${Math.round((part / whole) * 100)}%` : "—";
}

function dayLabel(isoDate) {
  // isoDate is a UTC calendar day ("2026-10-06"); format it without a timezone shift.
  return new Date(`${isoDate}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

export default function AdminOverview() {
  const { errorMessage } = useAdminAuth();
  const [overview, setOverview] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    adminApi
      .getOverview({ ensembleSince: ENSEMBLE_SINCE.toISOString() })
      .then((data) => !cancelled && setOverview(data))
      .catch((err) => !cancelled && setError(errorMessage(err, "Couldn't load the overview.")));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const { users, predictions, forecasts, daily, system } = overview || {};
  const forecastTotal = forecasts ? forecasts.ensemble + forecasts.catboost_only : 0;
  // Days before the ensemble launch can't be split by model (see ENSEMBLE_SINCE).
  const ensembleDays = useMemo(
    () => (daily ? daily.filter((d) => new Date(`${d.day}T23:59:59Z`) >= ENSEMBLE_SINCE) : []),
    [daily],
  );

  return (
    <AdminLayout active="overview" title="Overview" subtitle="Accounts, predictions and system health at a glance.">
      <ErrorBanner message={error} />
      {!overview && !error && <LoadingBlocks tiles={4} panels={2} />}

      {overview && (
        <>
          <SystemStrip system={system} />

          <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
            <StatTile
              icon="group"
              label="Users"
              value={formatCompact(users.total)}
              sub={`${users.new_7d} new this week · ${users.new_30d} this month`}
            />
            <StatTile
              icon="block"
              label="Disabled accounts"
              value={formatCompact(users.disabled)}
              sub={`${users.active} active`}
            />
            <StatTile
              icon="query_stats"
              label="Predictions"
              value={formatCompact(predictions.total)}
              sub={`${predictions.complete} complete · ${predictions.draft} drafts · ${predictions.last_7d} this week`}
            />
            <StatTile
              icon="hub"
              label="Used channel history"
              value={percent(forecasts.ensemble, forecastTotal)}
              sub={`${forecasts.ensemble} of ${forecastTotal} forecasts since the ensemble launch`}
            />
          </section>

          <section className="grid grid-cols-1 sm:grid-cols-2 gap-6">
            <StatTile
              icon="link"
              label="Users with a linked channel"
              value={percent(users.with_channel, users.total)}
              sub={`${users.with_channel} of ${users.total}`}
            />
            <StatTile
              icon="sync_problem"
              label="Channel fetch errors"
              value={formatCompact(users.with_fetch_error)}
              sub="Users whose last channel refresh failed"
            />
          </section>

          <DailyChart
            title="Signups and predictions, last 30 days"
            type="line"
            days={daily}
            series={ACTIVITY_SERIES}
          />

          {ensembleDays.length > 0 ? (
            <DailyChart
              title="Forecasts by model"
              note="Complete predictions per day since the ensemble launch. CatBoost only means the channel history cache wasn't warm yet."
              type="bar"
              stacked
              days={ensembleDays}
                series={MODEL_SERIES}
            />
          ) : null}
        </>
      )}
    </AdminLayout>
  );
}

function SystemStrip({ system }) {
  const items = [
    { label: "Database", ok: system.database === "ok", detail: system.database === "ok" ? "Connected" : system.database },
    {
      label: "Forecast model",
      ok: system.model_ready,
      detail: system.model_ready
        ? `Loaded on ${system.model_device || "unknown device"}${
            system.model_load_time_seconds != null ? ` in ${system.model_load_time_seconds.toFixed(1)}s` : ""
          }`
        : system.model_error || "Not loaded",
    },
  ];
  return (
    <section className="glass-panel rounded-xl p-4 flex flex-wrap gap-x-10 gap-y-3">
      {items.map((item) => (
        <div key={item.label} className="flex items-center gap-2">
          <span className={`material-symbols-outlined fill ${item.ok ? "text-primary" : "text-error"}`}>
            {item.ok ? "check_circle" : "error"}
          </span>
          <span className="font-label-md text-label-md text-on-surface">{item.label}</span>
          <span className="font-body-md text-body-md text-on-surface-variant">{item.detail}</span>
        </div>
      ))}
    </section>
  );
}

function DailyChart({ title, note, type, stacked = false, days, series }) {
  const canvasRef = useRef(null);
  const chartRef = useRef(null);
  const [showTable, setShowTable] = useState(false);
  const { theme } = useTheme();

  useEffect(() => {
    if (!canvasRef.current || showTable) return;
    const isDark = theme === "dark";
    const base = chartColors(isDark);
    const palette = seriesColors(isDark);
    const colors = [palette.a, palette.b];

    chartRef.current = new Chart(canvasRef.current.getContext("2d"), {
      type,
      data: {
        labels: days.map((d) => dayLabel(d.day)),
        datasets: series.map((s, i) =>
          type === "line"
            ? {
                label: s.label,
                data: days.map((d) => d[s.key]),
                borderColor: colors[i],
                backgroundColor: colors[i],
                borderWidth: 2,
                pointRadius: 0,
                pointHoverRadius: 5,
                pointHoverBorderColor: palette.surface,
                pointHoverBorderWidth: 2,
                tension: 0.3,
              }
            : {
                label: s.label,
                data: days.map((d) => d[s.key]),
                backgroundColor: colors[i],
                borderColor: palette.surface,
                borderWidth: { top: 2 },
                borderRadius: i === series.length - 1 ? { topLeft: 4, topRight: 4 } : 0,
                borderSkipped: "bottom",
                maxBarThickness: 28,
              },
        ),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { intersect: false, mode: "index" },
        plugins: {
          legend: {
            position: "top",
            align: "start",
            labels: { color: base.tick, font: { family: "Inter", size: 12 }, boxWidth: 12, boxHeight: 12, usePointStyle: true },
          },
          tooltip: {
            backgroundColor: base.tooltipBg,
            titleFont: { family: "Geist", size: 13 },
            bodyFont: { family: "Inter", size: 13 },
            padding: 10,
            cornerRadius: 8,
          },
        },
        scales: {
          y: {
            beginAtZero: true,
            stacked,
            grid: { color: base.grid },
            border: { display: false },
            ticks: { font: { family: "Geist", size: 11 }, color: base.tick, precision: 0 },
          },
          x: {
            stacked,
            grid: { display: false },
            border: { display: false },
            ticks: { font: { family: "Geist", size: 11 }, color: base.tick, maxRotation: 0, autoSkipPadding: 12 },
          },
        },
      },
    });
    return () => chartRef.current?.destroy();
  }, [days, series, type, stacked, theme, showTable]);

  return (
    <section className="glass-panel rounded-2xl p-6 md:p-8 shadow-[0_10px_30px_rgba(0,0,0,0.04)] flex flex-col gap-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="font-headline-md text-headline-md text-on-background">{title}</h3>
          {note && <p className="font-body-md text-body-md text-on-surface-variant mt-1">{note}</p>}
        </div>
        <button
          type="button"
          onClick={() => setShowTable((v) => !v)}
          className="shrink-0 flex items-center gap-1 px-3 py-1.5 rounded-lg border border-outline-variant font-label-md text-label-md text-on-surface-variant hover:bg-surface-container-high"
        >
          <span className="material-symbols-outlined text-[18px]">{showTable ? "show_chart" : "table_rows"}</span>
          {showTable ? "Chart" : "Table"}
        </button>
      </div>

      {showTable ? (
        <div className="max-h-[320px] overflow-auto">
          <table className="w-full text-left font-body-md text-body-md">
            <thead className="text-on-surface-variant font-label-md text-label-md sticky top-0 bg-surface">
              <tr>
                <th className="py-2 pr-4">Day</th>
                {series.map((s) => (
                  <th key={s.key} className="py-2 pr-4 text-right">
                    {s.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[...days].reverse().map((d) => (
                <tr key={d.day} className="border-t border-outline-variant/40">
                  <td className="py-2 pr-4">{dayLabel(d.day)}</td>
                  {series.map((s) => (
                    <td key={s.key} className="py-2 pr-4 text-right tabular-nums">
                      {d[s.key]}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="w-full h-[300px] relative">
          <canvas ref={canvasRef} aria-label={title} role="img" />
        </div>
      )}
    </section>
  );
}
