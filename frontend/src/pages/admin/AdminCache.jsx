import { useEffect, useState } from "react";
import AdminLayout, { ErrorBanner, LoadingBlocks, Pagination, StatTile } from "../../components/admin/AdminLayout";
import { useAdminAuth } from "../../context/AdminAuthContext";
import * as adminApi from "../../lib/adminApi";
import { formatDateTime } from "../../lib/adminFormat";
import { ENSEMBLE_SINCE, formatRelativeTime } from "../../lib/format";

const PAGE_SIZE = 25;
const STATUSES = [
  { key: "all", label: "All" },
  { key: "fresh", label: "Fresh" },
  { key: "stale", label: "Stale" },
  { key: "never", label: "Never warmed" },
  { key: "error", label: "Errors" },
];

const STATUS_STYLE = {
  fresh: { icon: "check_circle", text: "Fresh", className: "text-on-surface bg-surface-container-high", iconClass: "text-primary" },
  stale: { icon: "schedule", text: "Stale", className: "text-on-surface bg-surface-container-high", iconClass: "text-tertiary" },
  never: { icon: "hourglass_empty", text: "Never warmed", className: "text-on-surface bg-surface-container-high", iconClass: "text-tertiary" },
};

const buttonClass =
  "flex items-center gap-1 px-3 py-1.5 rounded-lg border border-outline-variant font-label-md text-label-md text-on-surface hover:bg-surface-container-high disabled:opacity-50 disabled:cursor-not-allowed";

export default function AdminCache() {
  const { errorMessage } = useAdminAuth();
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [offset, setOffset] = useState(0);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(null);
  const [purging, setPurging] = useState(null);

  useEffect(() => {
    const id = setTimeout(() => {
      setQ(search.trim());
      setOffset(0);
    }, 300);
    return () => clearTimeout(id);
  }, [search]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    adminApi
      .listCache({ q, status, ensembleSince: ENSEMBLE_SINCE.toISOString(), limit: PAGE_SIZE, offset })
      .then((data) => {
        if (!cancelled) {
          setResult(data);
          setError(null);
        }
      })
      .catch((err) => !cancelled && setError(errorMessage(err, "Couldn't load the cache.")))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, status, offset, reload]);

  async function act(key, call, message) {
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      const response = await call();
      setNotice(typeof message === "function" ? message(response) : message);
      setPurging(null);
      setReload((n) => n + 1);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  const summary = result?.summary;

  return (
    <AdminLayout
      active="cache"
      title="Channel cache"
      subtitle={`Encoded upload history that the HistAttnV2 half of the ensemble reads. Entries go stale after ${
        result ? result.ttl_hours : 24
      }h or when the encoder changes; without a warm entry a forecast is CatBoost only.`}
      actions={
        <button
          type="button"
          className="btn-primary px-4 py-2 rounded-xl font-label-md text-label-md flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
          disabled={!result?.model_ready || busy !== null}
          onClick={() =>
            act("stale", adminApi.warmStaleChannels, (r) =>
              r.queued ? `Queued ${r.queued} channel${r.queued === 1 ? "" : "s"} to re-warm. This runs in the background.` : "Nothing needs warming.",
            )
          }
        >
          <span className="material-symbols-outlined text-[18px]">local_fire_department</span>
          {busy === "stale" ? "Queueing…" : "Warm everything stale"}
        </button>
      }
    >
      {result && !result.model_ready && (
        <div className="flex items-center gap-2 rounded-lg bg-surface-container-high px-4 py-3 text-on-surface">
          <span className="material-symbols-outlined text-tertiary">info</span>
          The forecast model isn&apos;t loaded yet, so channels can&apos;t be warmed and encoder mismatches can&apos;t be checked.
        </div>
      )}
      <ErrorBanner message={error} />
      {notice && (
        <div className="rounded-lg bg-primary-fixed/40 text-on-primary-fixed px-4 py-3 font-body-md text-body-md text-sm">
          {notice}
        </div>
      )}

      {!result && loading && <LoadingBlocks tiles={4} panels={1} />}

      {summary && (
        <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
          <StatTile icon="database" label="Channels" value={summary.total} sub={`${summary.fresh} fresh`} />
          <StatTile icon="schedule" label="Stale or never warmed" value={summary.stale + summary.never} sub={`${summary.stale} stale · ${summary.never} never`} />
          <StatTile icon="error" label="Last warm failed" value={summary.with_error} />
          <StatTile
            icon="hub"
            label="Cache hit rate, last 7 days"
            value={summary.forecasts_7d ? `${Math.round((summary.hits_7d / summary.forecasts_7d) * 100)}%` : "—"}
            sub={`${summary.hits_7d} of ${summary.forecasts_7d} forecasts used channel history`}
          />
        </section>
      )}

      <div className="flex flex-wrap items-center gap-4">
        <div className="relative flex-1 min-w-[220px] max-w-md">
          <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant">
            search
          </span>
          <input
            className="w-full bg-surface-container-low border border-surface-variant rounded-full py-2 pl-10 pr-4 font-body-md text-body-md focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary"
            placeholder="Channel title or ID"
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="flex flex-wrap gap-1 bg-surface-container-low rounded-full p-1">
          {STATUSES.map((s) => (
            <button
              key={s.key}
              type="button"
              onClick={() => {
                setStatus(s.key);
                setOffset(0);
              }}
              className={`px-4 py-1.5 rounded-full font-label-md text-label-md transition-colors ${
                status === s.key ? "bg-primary text-on-primary" : "text-on-surface-variant hover:bg-surface-container-high"
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>

      {result && (
        <section className={`glass-panel rounded-2xl overflow-hidden ${loading ? "opacity-60" : ""}`}>
          <div className="overflow-x-auto">
            <table className="w-full text-left font-body-md text-body-md">
              <thead className="text-on-surface-variant font-label-md text-label-md border-b border-outline-variant/50">
                <tr>
                  <th className="px-6 py-3">Channel</th>
                  <th className="px-6 py-3">Status</th>
                  <th className="px-6 py-3">Last warmed</th>
                  <th className="px-6 py-3 text-right">Videos</th>
                  <th className="px-6 py-3 text-right">Users</th>
                  <th className="px-6 py-3">Actions</th>
                </tr>
              </thead>
              <tbody>
                {result.items.map((c) => {
                  const style = STATUS_STYLE[c.status];
                  return (
                    <tr key={c.channel_id} className="border-t border-outline-variant/30 align-top">
                      <td className="px-6 py-3">
                        <span className="font-label-md text-label-md text-on-surface">{c.channel_title || "Unknown title"}</span>
                        <span className="block text-sm text-on-surface-variant font-mono">{c.channel_id}</span>
                        {c.last_error && (
                          <span className="flex items-start gap-1 mt-1 text-sm text-error">
                            <span className="material-symbols-outlined text-[16px]">error</span>
                            {c.last_error}
                          </span>
                        )}
                      </td>
                      <td className="px-6 py-3">
                        <span className={`inline-flex items-center gap-1 font-label-sm text-label-sm px-2.5 py-1 rounded-full ${style.className}`}>
                          <span className={`material-symbols-outlined text-[16px] ${style.iconClass}`}>{style.icon}</span>
                          {style.text}
                        </span>
                        {c.encoder_matches === false && (
                          <span className="block text-sm text-on-surface-variant mt-1">Encoded with an older encoder</span>
                        )}
                      </td>
                      <td className="px-6 py-3 whitespace-nowrap" title={formatDateTime(c.warmed_at)}>
                        {c.warmed_at ? formatRelativeTime(c.warmed_at) : "—"}
                      </td>
                      <td className="px-6 py-3 text-right tabular-nums">{c.video_count}</td>
                      <td className="px-6 py-3 text-right tabular-nums">{c.linked_users}</td>
                      <td className="px-6 py-3">
                        {purging === c.channel_id ? (
                          <div className="flex flex-col gap-2">
                            <span className="text-sm">Forecasts go CatBoost-only until re-warmed.</span>
                            <div className="flex gap-2">
                              <button
                                type="button"
                                className="px-3 py-1.5 rounded-lg bg-error text-on-error font-label-md text-label-md disabled:opacity-50"
                                disabled={busy !== null}
                                onClick={() => act(`purge:${c.channel_id}`, () => adminApi.purgeChannel(c.channel_id), "Cache entry purged.")}
                              >
                                Purge
                              </button>
                              <button type="button" className={buttonClass} onClick={() => setPurging(null)}>
                                Cancel
                              </button>
                            </div>
                          </div>
                        ) : (
                          <div className="flex gap-2">
                            <button
                              type="button"
                              className={buttonClass}
                              disabled={!result.model_ready || busy !== null}
                              onClick={() =>
                                act(`warm:${c.channel_id}`, () => adminApi.warmChannel(c.channel_id), "Warm queued. It runs in the background; reload in a minute.")
                              }
                            >
                              <span className="material-symbols-outlined text-[16px]">refresh</span>
                              Warm
                            </button>
                            {c.has_entry && (
                              <button type="button" className={buttonClass} disabled={busy !== null} onClick={() => setPurging(c.channel_id)}>
                                <span className="material-symbols-outlined text-[16px]">delete</span>
                                Purge
                              </button>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
                {result.items.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-6 py-10 text-center text-on-surface-variant">
                      No channels match.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {result && <Pagination total={result.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} />}
    </AdminLayout>
  );
}
