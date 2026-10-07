// Formatting helpers shared by the admin dashboard pages.

export function formatDateTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
}

export function formatDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-US", { dateStyle: "medium" });
}

const ACTION_LABELS = {
  "admin.login": "Signed in",
  "user.disable": "Disabled user",
  "user.enable": "Enabled user",
  "user.refresh_channel": "Refreshed channel",
  "user.clear_fetch_error": "Cleared fetch error",
  "user.delete": "Deleted user",
  "prediction.delete": "Deleted prediction",
  "cache.warm": "Warmed channel cache",
  "cache.warm_stale": "Warmed stale channels",
  "cache.purge": "Purged channel cache",
};

export function actionLabel(action) {
  return ACTION_LABELS[action] || action;
}

export const AUDIT_ACTIONS = Object.keys(ACTION_LABELS);
