import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import AdminLayout, { ErrorBanner, LoadingBlocks, Pagination, StatTile } from "../../components/admin/AdminLayout";
import SeriesChart from "../../components/admin/SeriesChart";
import { useAdminAuth } from "../../context/AdminAuthContext";
import * as adminApi from "../../lib/adminApi";
import { fileUrl } from "../../lib/api";
import { formatDateTime } from "../../lib/adminFormat";
import { ENSEMBLE_SINCE, formatCompact, modelLabel } from "../../lib/format";

const PAGE_SIZE = 25;
const FILTER_KEYS = ["q", "status", "model", "category", "user_id", "date_from", "date_to"];

const selectClass =
  "bg-surface-container-low border border-outline-variant rounded-lg px-3 py-2 font-body-md text-body-md text-on-surface";

function percentText(value) {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

function confidenceLabel(v) {
  return `${Math.round(v * 100)}%`;
}

export default function AdminPredictions() {
  const { errorMessage } = useAdminAuth();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get("q") || "");
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const filters = useMemo(
    () => Object.fromEntries(FILTER_KEYS.map((k) => [k, params.get(k) || ""])),
    [params],
  );
  const offset = Number(params.get("offset") || 0);

  function update(changes) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value === "" || value == null) next.delete(key);
      else next.set(key, value);
    }
    if (!("offset" in changes)) next.delete("offset");
    setParams(next, { replace: true });
  }

  // Debounce the search box into the URL.
  useEffect(() => {
    if (search.trim() === filters.q) return undefined;
    const id = setTimeout(() => update({ q: search.trim() }), 300);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    adminApi
      .listAdminPredictions({
        ...filters,
        ensemble_since: ENSEMBLE_SINCE.toISOString(),
        limit: PAGE_SIZE,
        offset,
      })
      .then((data) => {
        if (!cancelled) {
          setResult(data);
          setError(null);
        }
      })
      .catch((err) => !cancelled && setError(errorMessage(err, "Couldn't load predictions.")))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, offset]);

  const summary = result?.summary;
  const viewsChart = useMemo(
    () =>
      summary && {
        labels: summary.views_histogram.map((b) => `${formatCompact(b.low)}–${formatCompact(b.high)}`),
        values: summary.views_histogram.map((b) => b.count),
      },
    [summary],
  );
  const confidenceChart = useMemo(
    () =>
      summary && {
        labels: summary.confidence_histogram.map((b) => `${confidenceLabel(b.low)}–${confidenceLabel(b.high)}`),
        values: summary.confidence_histogram.map((b) => b.count),
      },
    [summary],
  );
  const hasFilters = FILTER_KEYS.some((k) => filters[k]);

  return (
    <AdminLayout
      active="predictions"
      title="Predictions"
      subtitle="Every user's predictions. The summary covers everything matching the filters, not just this page."
    >
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[220px] max-w-sm">
          <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant">
            search
          </span>
          <input
            className="w-full bg-surface-container-low border border-surface-variant rounded-full py-2 pl-10 pr-4 font-body-md text-body-md focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary"
            placeholder="Title, user name or email"
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select className={selectClass} value={filters.status} onChange={(e) => update({ status: e.target.value })} aria-label="Status">
          <option value="">Any status</option>
          <option value="complete">Complete</option>
          <option value="draft">Draft</option>
        </select>
        <select className={selectClass} value={filters.model} onChange={(e) => update({ model: e.target.value })} aria-label="Model">
          <option value="">Any model</option>
          <option value="ensemble">Ensemble (with channel history)</option>
          <option value="catboost_only">CatBoost only</option>
        </select>
        <select
          className={selectClass}
          value={filters.category}
          onChange={(e) => update({ category: e.target.value })}
          aria-label="Category"
        >
          <option value="">Any category</option>
          {result?.all_categories.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-2 font-label-md text-label-md text-on-surface-variant">
          From
          <input type="date" className={selectClass} value={filters.date_from} onChange={(e) => update({ date_from: e.target.value })} />
        </label>
        <label className="flex items-center gap-2 font-label-md text-label-md text-on-surface-variant">
          To
          <input type="date" className={selectClass} value={filters.date_to} onChange={(e) => update({ date_to: e.target.value })} />
        </label>
        {filters.user_id && (
          <span className="flex items-center gap-1 bg-primary-fixed/40 text-on-primary-fixed rounded-full pl-3 pr-1 py-1 font-label-md text-label-md">
            User #{filters.user_id}
            <button type="button" onClick={() => update({ user_id: "" })} aria-label="Clear user filter" className="material-symbols-outlined text-[18px] px-1">
              close
            </button>
          </span>
        )}
        {hasFilters && (
          <button
            type="button"
            className="font-label-md text-label-md text-primary hover:underline"
            onClick={() => {
              setSearch("");
              setParams(new URLSearchParams(), { replace: true });
            }}
          >
            Clear filters
          </button>
        )}
      </div>

      <ErrorBanner message={error} />
      {!result && loading && <LoadingBlocks tiles={4} panels={1} />}

      {summary && (
        <div className={`flex flex-col gap-6 transition-opacity ${loading ? "opacity-60" : ""}`}>
          <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
            <StatTile icon="filter_alt" label="Matching predictions" value={formatCompact(summary.count)} sub={`${summary.complete} complete`} />
            <StatTile icon="visibility" label="Median predicted views" value={formatCompact(summary.median_views)} />
            <StatTile icon="trending_up" label="90th percentile views" value={formatCompact(summary.p90_views)} />
            <StatTile icon="verified" label="Average confidence" value={percentText(summary.avg_confidence)} />
          </section>

          {summary.complete > 0 && (
            <section className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <div className="glass-panel rounded-2xl p-6 flex flex-col gap-4">
                <div>
                  <h3 className="font-headline-md text-headline-md text-on-background">Predicted views</h3>
                  <p className="font-body-md text-body-md text-on-surface-variant mt-1">
                    Complete predictions per order of magnitude. A single tall bar can mean the model output has collapsed.
                  </p>
                </div>
                <SeriesChart labels={viewsChart.labels} values={viewsChart.values} label="Predictions" />
              </div>
              <div className="glass-panel rounded-2xl p-6 flex flex-col gap-4">
                <div>
                  <h3 className="font-headline-md text-headline-md text-on-background">Confidence</h3>
                  <p className="font-body-md text-body-md text-on-surface-variant mt-1">
                    Derived from the width of the forecast&apos;s uncertainty band, not a calibrated probability.
                  </p>
                </div>
                <SeriesChart labels={confidenceChart.labels} values={confidenceChart.values} label="Predictions" />
              </div>
            </section>
          )}

          <section className="glass-panel rounded-2xl overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-left font-body-md text-body-md">
                <thead className="text-on-surface-variant font-label-md text-label-md border-b border-outline-variant/50">
                  <tr>
                    <th className="px-6 py-3">Prediction</th>
                    <th className="px-6 py-3">User</th>
                    <th className="px-6 py-3">Status</th>
                    <th className="px-6 py-3 text-right">Predicted views</th>
                    <th className="px-6 py-3 text-right">Confidence</th>
                    <th className="px-6 py-3">Model</th>
                    <th className="px-6 py-3">Created</th>
                  </tr>
                </thead>
                <tbody>
                  {result.items.map((p) => (
                    <tr key={p.id} className="border-t border-outline-variant/30 hover:bg-surface-container-low/60">
                      <td className="px-6 py-3">
                        <Link to={`/admin/predictions/${p.id}`} className="flex items-center gap-3 hover:text-primary">
                          {p.thumbnail_url ? (
                            <img src={fileUrl(p.thumbnail_url)} alt="" className="w-16 h-9 rounded object-cover shrink-0" />
                          ) : (
                            <span className="w-16 h-9 rounded bg-surface-container-high shrink-0" />
                          )}
                          <span>
                            <span className="font-label-md text-label-md text-on-surface">{p.title}</span>
                            <span className="block text-on-surface-variant text-sm">{p.category || "No category"}</span>
                          </span>
                        </Link>
                      </td>
                      <td className="px-6 py-3">
                        <Link to={`/admin/users/${p.user_id}`} className="hover:text-primary">
                          {p.user_name}
                          <span className="block text-on-surface-variant text-sm">{p.user_email}</span>
                        </Link>
                      </td>
                      <td className="px-6 py-3 capitalize">{p.status}</td>
                      <td className="px-6 py-3 text-right tabular-nums">{formatCompact(p.predicted_views)}</td>
                      <td className="px-6 py-3 text-right tabular-nums">{percentText(p.confidence)}</td>
                      <td className="px-6 py-3 text-sm text-on-surface-variant">{modelLabel(p) || "—"}</td>
                      <td className="px-6 py-3 whitespace-nowrap">{formatDateTime(p.created_at)}</td>
                    </tr>
                  ))}
                  {result.items.length === 0 && (
                    <tr>
                      <td colSpan={7} className="px-6 py-10 text-center text-on-surface-variant">
                        No predictions match.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <Pagination total={result.total} limit={PAGE_SIZE} offset={offset} onChange={(o) => update({ offset: o })} />
        </div>
      )}
    </AdminLayout>
  );
}
