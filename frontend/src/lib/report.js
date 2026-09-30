// Builds a standalone HTML report for one prediction and downloads it. No
// dependencies: the file opens in any browser and prints cleanly to PDF.

import { formatSignedPercent, modelLabel } from "./format";

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ESCAPES[ch]);
}

const number = (value) =>
  value === null || value === undefined ? "—" : Math.round(value).toLocaleString("en-US");

function trajectorySvg(trajectory) {
  const width = 640;
  const height = 240;
  const pad = { top: 16, right: 16, bottom: 28, left: 56 };
  const maxViews = Math.max(...trajectory.map((p) => p.views), 1);
  const x = (i) => pad.left + (i * (width - pad.left - pad.right)) / Math.max(trajectory.length - 1, 1);
  const y = (v) => pad.top + (1 - v / maxViews) * (height - pad.top - pad.bottom);

  const points = trajectory.map((p, i) => `${x(i).toFixed(1)},${y(p.views).toFixed(1)}`).join(" ");
  const gridLines = [0, 0.25, 0.5, 0.75, 1]
    .map((f) => {
      const value = maxViews * f;
      return `<line x1="${pad.left}" x2="${width - pad.right}" y1="${y(value)}" y2="${y(value)}" stroke="#e5e5ea"/>
        <text x="${pad.left - 8}" y="${y(value) + 4}" text-anchor="end" font-size="11" fill="#6b6b76">${escapeHtml(
          `${(value / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 })}K`,
        )}</text>`;
    })
    .join("");
  const dayLabels = trajectory
    .map((p, i) => `<text x="${x(i)}" y="${height - 8}" text-anchor="middle" font-size="11" fill="#6b6b76">Day ${p.day}</text>`)
    .join("");
  const dots = trajectory
    .map((p, i) => `<circle cx="${x(i)}" cy="${y(p.views)}" r="4" fill="#fff" stroke="#6d5dfc" stroke-width="2"/>`)
    .join("");

  return `<svg viewBox="0 0 ${width} ${height}" width="100%" role="img" aria-label="Predicted cumulative views by day">
    ${gridLines}${dayLabels}
    <polyline points="${points}" fill="none" stroke="#6d5dfc" stroke-width="2.5"/>
    ${dots}
  </svg>`;
}

export function buildPredictionReport(prediction) {
  const trajectory = prediction.trajectory ?? [];
  const change = formatSignedPercent(prediction.change_vs_avg);
  const rows = trajectory
    .map((p) => `<tr><td>Day ${p.day}</td><td>${number(p.views)}</td></tr>`)
    .join("");
  const summary = [
    ["Predicted 7-day views", number(prediction.predicted_views)],
    ["Change vs channel average", change],
    ["Confidence", prediction.confidence === null ? "—" : `${Math.round(prediction.confidence * 100)}%`],
    ["Model", modelLabel(prediction) ?? "—"],
    ["Category", prediction.category || "—"],
    ["Tags", prediction.tags?.length ? prediction.tags.join(", ") : "—"],
    ["Target date", [prediction.target_date, prediction.target_time].filter(Boolean).join(" ") || "—"],
    ["Created", new Date(prediction.created_at).toLocaleString()],
  ]
    .map(([k, v]) => `<tr><th>${escapeHtml(k)}</th><td>${escapeHtml(v)}</td></tr>`)
    .join("");

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TrendCast prediction: ${escapeHtml(prediction.title)}</title>
<style>
  body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; color: #1c1b22; background: #fff;
         max-width: 760px; margin: 32px auto; padding: 0 16px; line-height: 1.5; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .muted { color: #6b6b76; font-size: 13px; margin: 0 0 24px; }
  h2 { font-size: 15px; margin: 28px 0 8px; }
  table { border-collapse: collapse; width: 100%; font-size: 14px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #e5e5ea; }
  th { color: #6b6b76; font-weight: 500; width: 40%; }
  td:last-child { font-variant-numeric: tabular-nums; }
  .note { color: #6b6b76; font-size: 12px; margin-top: 24px; }
</style>
</head>
<body>
  <h1>${escapeHtml(prediction.title)}</h1>
  <p class="muted">TrendCast prediction report · generated ${escapeHtml(new Date().toLocaleString())}</p>
  <h2>Summary</h2>
  <table>${summary}</table>
  <h2>Predicted cumulative views</h2>
  ${trajectory.length ? trajectorySvg(trajectory) : "<p class=\"muted\">No trajectory for this prediction.</p>"}
  ${trajectory.length ? `<table><tr><th>Day</th><th>Cumulative views</th></tr>${rows}</table>` : ""}
  <p class="note">Confidence is derived from the width of the forecast's uncertainty band; it is a heuristic, not a calibrated probability.</p>
</body>
</html>`;
}

export function downloadPredictionReport(prediction) {
  const blob = new Blob([buildPredictionReport(prediction)], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `trendcast-prediction-${prediction.id}.html`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
