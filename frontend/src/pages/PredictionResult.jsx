import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import Chart from "chart.js/auto";
import Sidebar from "../components/Sidebar";
import ResultsTopbar from "../components/ResultsTopbar";
import * as api from "../lib/api";
import { formatCompact, formatSignedPercent } from "../lib/format";
import { chartColors } from "../lib/chartTheme";
import { useTheme } from "../context/ThemeContext";

const PLACEHOLDER_THUMBNAIL =
  "https://lh3.googleusercontent.com/aida-public/AB6AXuDiLpShN4XgeCCGMTWX7S5lrv3G0lqBcxQjRjdTOCL1gCVpB__fhYEG_7YGolU-oSbAbmpChoPuCl7oI_lDLekL3xXjQbxhBTlTByJFaHrdgqzMLZR9_pyh4ryDGvgZMb_dMdCgja8lDxjZpI_q_pr0G8N_sRNDGLxyD3eCdlVaQzs3LS0HTO_y5XjmP_JVtwt_zGQWlQ_9KIskQ87a9p6xAiuaRsU2WpK21ZPvjQGnvYdNr407QAA";

export default function PredictionResult() {
  const { id } = useParams();
  const navigate = useNavigate();
  const canvasRef = useRef(null);
  const chartRef = useRef(null);
  const { theme } = useTheme();

  const [prediction, setPrediction] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    api
      .getPrediction(id)
      .then((data) => {
        if (!cancelled) setPrediction(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || "Couldn't load this prediction.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [id]);

  useEffect(() => {
    if (!prediction || !canvasRef.current) return;

    const trajectory = prediction.trajectory?.length
      ? prediction.trajectory
      : [];
    const labels = trajectory.map((p) => p.day);
    const data = trajectory.map((p) => p.views);

    const ctx = canvasRef.current.getContext("2d");
    const colors = chartColors(theme === "dark");

    const gradient = ctx.createLinearGradient(0, 0, 0, 400);
    gradient.addColorStop(0, colors.gradientTop);
    gradient.addColorStop(1, colors.gradientBottom);

    chartRef.current = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "Predicted Views",
            data,
            borderColor: colors.line,
            backgroundColor: gradient,
            borderWidth: 3,
            pointBackgroundColor: "#ffffff",
            pointBorderColor: colors.point,
            pointBorderWidth: 2,
            pointRadius: 5,
            pointHoverRadius: 7,
            fill: true,
            tension: 0.4,
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
            titleFont: { family: "Geist", size: 14 },
            bodyFont: { family: "Inter", size: 14 },
            padding: 12,
            cornerRadius: 8,
            displayColors: false,
            callbacks: {
              label: (context) => {
                let label = context.dataset.label || "";
                if (label) label += ": ";
                if (context.parsed.y !== null) {
                  label += new Intl.NumberFormat("en-US", {
                    notation: "compact",
                    compactDisplay: "short",
                  }).format(context.parsed.y);
                }
                return label;
              },
            },
          },
        },
        scales: {
          y: {
            beginAtZero: true,
            grid: { color: colors.grid, drawBorder: false },
            ticks: {
              font: { family: "Geist", size: 12 },
              color: colors.tick,
              callback: (value) => (value === 0 ? "0" : `${(value / 1000000).toFixed(1)}M`),
            },
          },
          x: {
            grid: { display: false, drawBorder: false },
            ticks: { font: { family: "Geist", size: 12 }, color: colors.tick },
          },
        },
        interaction: { intersect: false, mode: "index" },
      },
    });

    return () => chartRef.current?.destroy();
  }, [prediction, theme]);

  if (loading) {
    return (
      <div className="bg-surface min-h-screen flex items-center justify-center">
        <span className="material-symbols-outlined animate-spin text-4xl text-primary">progress_activity</span>
      </div>
    );
  }

  if (error || !prediction) {
    return (
      <div className="bg-surface min-h-screen flex flex-col items-center justify-center gap-4 text-center px-6">
        <span className="material-symbols-outlined text-4xl text-error">error</span>
        <p className="font-body-md text-body-md text-on-surface-variant">
          {error || "Prediction not found."}
        </p>
        <Link to="/dashboard" className="bg-primary text-white px-5 py-2.5 rounded-full font-label-md text-label-md">
          Back to Dashboard
        </Link>
      </div>
    );
  }

  const changeIcon = (prediction.change_vs_avg ?? 0) >= 0 ? "trending_up" : "trending_down";

  return (
    <div className="bg-surface text-on-surface font-body-md text-body-md antialiased min-h-screen">
      <ResultsTopbar />
      <Sidebar active="predictions" />

      <main className="md:ml-64 pt-24 px-margin-mobile md:px-margin-desktop pb-20 max-w-container-max mx-auto min-h-screen flex flex-col gap-12">
        <div className="flex flex-col md:flex-row justify-between items-start md:items-end gap-4 w-full">
          <div>
            <p className="font-label-md text-label-md text-primary uppercase tracking-wider mb-2 flex items-center gap-2">
              <span className="material-symbols-outlined text-sm">check_circle</span> Analysis Complete
            </p>
            <h2 className="font-headline-lg-mobile md:font-headline-lg text-headline-lg-mobile md:text-headline-lg text-on-surface">
              Prediction Results
            </h2>
          </div>
          <div className="flex gap-3 w-full md:w-auto">
            <button className="flex-1 md:flex-none px-6 py-2.5 rounded-lg border border-outline-variant text-on-surface-variant font-label-md text-label-md hover:bg-surface-container-high transition-colors flex items-center justify-center gap-2">
              <span className="material-symbols-outlined text-sm">download</span> Export Report
            </button>
            <button
              onClick={() => navigate("/new-prediction")}
              className="flex-1 md:flex-none btn-primary px-6 py-2.5 rounded-lg font-label-md text-label-md flex items-center justify-center gap-2 transition-all"
            >
              Submit Another
            </button>
          </div>
        </div>

        <section className="glass-panel rounded-2xl p-6 md:p-8 flex flex-col lg:flex-row gap-8 items-center shadow-[0_10px_30px_rgba(0,0,0,0.04)] hover:shadow-[0_20px_40px_rgba(0,0,0,0.08)] hover:scale-[1.005] transition-all duration-300">
          <div className="w-full lg:w-5/12 aspect-video rounded-xl overflow-hidden relative group">
            <img
              alt="Video thumbnail"
              className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105"
              src={api.fileUrl(prediction.thumbnail_url) || PLACEHOLDER_THUMBNAIL}
            />
          </div>

          <div className="w-full lg:w-7/12 flex flex-col gap-6">
            <div>
              <div className="flex flex-wrap gap-2 mb-3">
                {prediction.category && (
                  <span className="px-3 py-1 bg-primary-container/20 text-primary-fixed-dim rounded-full font-label-sm text-label-sm border border-primary-container/30">
                    {prediction.category}
                  </span>
                )}
                {prediction.tags.map((tag) => (
                  <span
                    key={tag}
                    className="px-3 py-1 bg-surface-container-highest text-on-surface-variant rounded-full font-label-sm text-label-sm"
                  >
                    {tag}
                  </span>
                ))}
              </div>
              <h3 className="font-headline-md text-headline-md text-on-surface leading-tight">
                {prediction.title}
              </h3>
              <p className="font-body-md text-body-md text-on-surface-variant mt-2">
                Predicted with {Math.round((prediction.confidence ?? 0) * 100)}% confidence
                {prediction.target_date ? ` · targeting ${prediction.target_date}` : ""}
              </p>
            </div>
            <div className="border-t border-outline-variant/30 pt-6 mt-auto">
              <p className="font-label-md text-label-md text-on-surface-variant mb-1">7-Day View Forecast</p>
              <div className="flex items-baseline gap-3">
                <span className="font-display-lg text-display-lg gradient-text">
                  {formatCompact(prediction.predicted_views)}
                </span>
                {prediction.change_vs_avg !== null && (
                  <span className="flex items-center text-secondary font-label-md text-label-md bg-secondary-container/20 px-2 py-1 rounded-md">
                    <span className="material-symbols-outlined text-sm mr-1">{changeIcon}</span>
                    {formatSignedPercent(prediction.change_vs_avg)} vs avg
                  </span>
                )}
              </div>
            </div>
          </div>
        </section>

        <section className="glass-panel rounded-2xl p-6 md:p-8 shadow-[0_10px_30px_rgba(0,0,0,0.04)]">
          <div className="flex justify-between items-center mb-6">
            <h4 className="font-headline-md text-headline-md text-on-surface">
              Predicted Trajectory
            </h4>
            <div className="flex gap-2 bg-surface-container rounded-lg p-1">
              <button className="px-4 py-1.5 rounded-md bg-surface text-on-surface font-label-sm shadow-sm">
                Views
              </button>
              <button className="px-4 py-1.5 rounded-md text-on-surface-variant font-label-sm hover:text-on-surface transition-colors">
                Engagement
              </button>
            </div>
          </div>
          <div className="w-full h-[400px] relative">
            <canvas ref={canvasRef} />
          </div>
        </section>
      </main>
    </div>
  );
}
