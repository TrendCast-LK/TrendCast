import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import Chart from "chart.js/auto";
import Sidebar from "../components/Sidebar";
import Topbar from "../components/Topbar";
import * as api from "../lib/api";
import { formatCompact } from "../lib/format";
import { chartColors } from "../lib/chartTheme";
import { useTheme } from "../context/ThemeContext";

export default function Trends() {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const canvasRef = useRef(null);
  const chartRef = useRef(null);
  const { theme } = useTheme();

  useEffect(() => {
    let cancelled = false;
    api
      .getTrendsSummary()
      .then((data) => {
        if (!cancelled) setSummary(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || "Couldn't load trends.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!summary || !canvasRef.current || summary.timeline.length === 0) return;

    const ctx = canvasRef.current.getContext("2d");
    const colors = chartColors(theme === "dark");
    const gradient = ctx.createLinearGradient(0, 0, 0, 320);
    gradient.addColorStop(0, colors.gradientTop);
    gradient.addColorStop(1, colors.gradientBottom);

    chartRef.current = new Chart(ctx, {
      type: "line",
      data: {
        labels: summary.timeline.map((p) => p.title),
        datasets: [
          {
            label: "Predicted Views",
            data: summary.timeline.map((p) => p.predicted_views),
            borderColor: colors.line,
            backgroundColor: gradient,
            borderWidth: 3,
            pointBackgroundColor: "#ffffff",
            pointBorderColor: colors.point,
            pointBorderWidth: 2,
            pointRadius: 5,
            pointHoverRadius: 7,
            fill: true,
            tension: 0.35,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: colors.tooltipBg,
            titleFont: { family: "Geist", size: 13 },
            bodyFont: { family: "Inter", size: 13 },
            padding: 10,
            cornerRadius: 8,
            displayColors: false,
            callbacks: {
              label: (context) => formatCompact(context.parsed.y),
            },
          },
        },
        scales: {
          y: {
            beginAtZero: true,
            grid: { color: colors.grid, drawBorder: false },
            ticks: { font: { family: "Geist", size: 11 }, color: colors.tick, callback: (v) => formatCompact(v) },
          },
          x: {
            grid: { display: false, drawBorder: false },
            ticks: {
              font: { family: "Geist", size: 11 },
              color: colors.tick,
              callback: function (value) {
                const label = this.getLabelForValue(value);
                return label.length > 14 ? `${label.slice(0, 14)}…` : label;
              },
            },
          },
        },
        interaction: { intersect: false, mode: "index" },
      },
    });

    return () => chartRef.current?.destroy();
  }, [summary, theme]);

  return (
    <div className="bg-background text-on-background min-h-screen font-body-md selection:bg-primary-fixed selection:text-on-primary-fixed">
      <Sidebar active="trends" />
      <Topbar searchPlaceholder="Search trends..." />

      <main className="ml-64 pt-24 px-margin-desktop pb-24 max-w-container-max mx-auto flex flex-col gap-10">
        <div>
          <p className="font-label-md text-label-md text-primary mb-2 uppercase tracking-widest">
            Performance Overview
          </p>
          <h2 className="font-headline-lg text-headline-lg text-on-background">Trends</h2>
          <p className="font-body-md text-body-md text-on-surface-variant mt-2">
            How your predictions are shaping up across categories and over time.
          </p>
        </div>

        {error && (
          <div className="rounded-lg bg-error-container/60 text-on-error-container px-4 py-3 font-body-md text-body-md text-sm">
            {error}
          </div>
        )}

        {!loading && summary && summary.completed_predictions === 0 && (
          <div className="glass-panel rounded-xl p-10 flex flex-col items-center text-center gap-3">
            <span className="material-symbols-outlined text-4xl text-primary">trending_up</span>
            <p className="font-body-md text-body-md text-on-surface-variant">
              No completed predictions yet — trends will show up here once you've run a few.
            </p>
            <Link
              to="/new-prediction"
              className="mt-2 bg-primary text-white px-5 py-2.5 rounded-full font-label-md text-label-md"
            >
              Create a prediction
            </Link>
          </div>
        )}

        {summary && summary.completed_predictions > 0 && (
          <>
            <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
              <StatTile
                icon="query_stats"
                label="Total Predictions"
                value={summary.total_predictions}
                sub={`${summary.draft_predictions} draft${summary.draft_predictions === 1 ? "" : "s"}`}
              />
              <StatTile
                icon="visibility"
                label="Avg. Predicted Views"
                value={formatCompact(summary.average_predicted_views)}
              />
              <StatTile
                icon="verified"
                label="Avg. Confidence"
                value={
                  summary.average_confidence !== null ? `${Math.round(summary.average_confidence * 100)}%` : "—"
                }
              />
              <StatTile icon="star" label="Top Category" value={summary.best_category || "—"} />
            </section>

            <section className="glass-panel rounded-2xl p-6 md:p-8 shadow-[0_10px_30px_rgba(0,0,0,0.04)]">
              <h3 className="font-headline-md text-headline-md text-on-background mb-6">
                Predicted Views Over Time
              </h3>
              <div className="w-full h-[320px] relative">
                <canvas ref={canvasRef} />
              </div>
            </section>

            <section className="glass-panel rounded-2xl p-6 md:p-8 shadow-[0_10px_30px_rgba(0,0,0,0.04)]">
              <h3 className="font-headline-md text-headline-md text-on-background mb-6">Category Breakdown</h3>
              <div className="flex flex-col gap-4">
                {summary.category_breakdown.map((c) => {
                  const max = summary.category_breakdown[0].average_views || 1;
                  const width = Math.max(6, Math.round((c.average_views / max) * 100));
                  return (
                    <div key={c.category} className="flex flex-col gap-1.5">
                      <div className="flex items-center justify-between">
                        <span className="font-label-md text-label-md text-on-surface">
                          {c.category}{" "}
                          <span className="text-on-surface-variant font-body-md text-body-md">
                            ({c.count} prediction{c.count === 1 ? "" : "s"})
                          </span>
                        </span>
                        <span className="font-label-md text-label-md text-primary">
                          {formatCompact(c.average_views)} avg
                        </span>
                      </div>
                      <div className="w-full bg-surface-container-highest rounded-full h-2 overflow-hidden">
                        <div
                          className="bg-gradient-to-r from-primary to-secondary h-2 rounded-full transition-all duration-500"
                          style={{ width: `${width}%` }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  );
}

function StatTile({ icon, label, value, sub }) {
  return (
    <div className="glass-panel rounded-xl p-6 flex flex-col gap-2">
      <span className="material-symbols-outlined text-primary">{icon}</span>
      <span className="font-headline-lg text-headline-lg text-on-background">{value}</span>
      <span className="font-label-md text-label-md text-on-surface-variant">{label}</span>
      {sub && <span className="font-label-sm text-label-sm text-tertiary">{sub}</span>}
    </div>
  );
}
