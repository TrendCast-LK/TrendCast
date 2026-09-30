export function formatCompact(value) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-US", { notation: "compact", compactDisplay: "short" }).format(
    value,
  );
}

// value is a ratio (0.25 -> "+25%"), as the backend stores change_vs_avg.
export function formatSignedPercent(value) {
  if (value === null || value === undefined) return "—";
  const rounded = Math.round(value * 1000) / 10;
  const sign = rounded > 0 ? "+" : "";
  return `${sign}${rounded}%`;
}

// Chart ticks in thousands: 3400 -> "3.4K", 250 -> "0.25K".
export function formatThousands(value) {
  if (value === 0) return "0";
  return `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value / 1000)}K`;
}

// When the CatBoost + HistAttnV2 ensemble went live. Before it, the backend
// saved used_channel_context = true on every forecast (CatBoost alone), so the
// flag can't tell the models apart for older predictions. Override with
// VITE_ENSEMBLE_SINCE (an ISO timestamp) in frontend/.env.
export const ENSEMBLE_SINCE = new Date(import.meta.env?.VITE_ENSEMBLE_SINCE || "2026-09-30T00:00:00Z");

// Which forecast model produced a prediction, or null when that isn't known
// (drafts, and anything created before the ensemble went live).
export function modelLabel(prediction) {
  if (!prediction || new Date(prediction.created_at) < ENSEMBLE_SINCE) return null;
  if (prediction.used_channel_context === true) return "Ensemble (CatBoost + channel history)";
  if (prediction.used_channel_context === false) return "CatBoost only (channel history not ready yet)";
  return null;
}

const RELATIVE_UNITS = [
  ["year", 31536000],
  ["month", 2592000],
  ["day", 86400],
  ["hour", 3600],
  ["minute", 60],
];
const relativeFormatter = new Intl.RelativeTimeFormat("en-US", { numeric: "auto" });

export function formatRelativeTime(isoString) {
  const then = new Date(isoString).getTime();
  const seconds = Math.round((then - Date.now()) / 1000);
  const abs = Math.abs(seconds);

  if (abs < 60) return "just now";
  for (const [unit, secondsInUnit] of RELATIVE_UNITS) {
    if (abs >= secondsInUnit) {
      return relativeFormatter.format(Math.round(seconds / secondsInUnit), unit);
    }
  }
  return relativeFormatter.format(Math.round(seconds / 60), "minute");
}
