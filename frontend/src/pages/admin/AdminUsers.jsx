import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import AdminLayout, { ErrorBanner, Pagination, StatusPill } from "../../components/admin/AdminLayout";
import { formatDate } from "../../lib/adminFormat";
import { useAdminAuth } from "../../context/AdminAuthContext";
import * as adminApi from "../../lib/adminApi";
import { formatCompact, formatRelativeTime } from "../../lib/format";

const PAGE_SIZE = 25;
const STATUSES = [
  { key: "all", label: "All" },
  { key: "active", label: "Active" },
  { key: "disabled", label: "Disabled" },
];

export default function AdminUsers() {
  const { errorMessage } = useAdminAuth();
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [offset, setOffset] = useState(0);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Debounce typing into the query actually sent.
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
      .listUsers({ q, status, limit: PAGE_SIZE, offset })
      .then((data) => {
        if (!cancelled) {
          setResult(data);
          setError(null);
        }
      })
      .catch((err) => !cancelled && setError(errorMessage(err, "Couldn't load users.")))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, status, offset]);

  return (
    <AdminLayout active="users" title="Users" subtitle="Search accounts, check their channel link, and manage access.">
      <div className="flex flex-wrap items-center gap-4">
        <div className="relative flex-1 min-w-[240px] max-w-md">
          <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant">
            search
          </span>
          <input
            className="w-full bg-surface-container-low border border-surface-variant rounded-full py-2 pl-10 pr-4 font-body-md text-body-md focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
            placeholder="Name, email or channel"
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="flex gap-1 bg-surface-container-low rounded-full p-1">
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

      <ErrorBanner message={error} />

      <section className="glass-panel rounded-2xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left font-body-md text-body-md">
            <thead className="text-on-surface-variant font-label-md text-label-md border-b border-outline-variant/50">
              <tr>
                <th className="px-6 py-3">User</th>
                <th className="px-6 py-3">Channel</th>
                <th className="px-6 py-3 text-right">Subscribers</th>
                <th className="px-6 py-3 text-right">Predictions</th>
                <th className="px-6 py-3">Joined</th>
                <th className="px-6 py-3">Status</th>
              </tr>
            </thead>
            <tbody className={loading ? "opacity-60" : ""}>
              {result?.items.map((u) => (
                <tr key={u.id} className="border-t border-outline-variant/30 hover:bg-surface-container-low/60">
                  <td className="px-6 py-3">
                    <Link to={`/admin/users/${u.id}`} className="block hover:text-primary">
                      <span className="font-label-md text-label-md text-on-surface">{u.full_name}</span>
                      <span className="block text-on-surface-variant text-sm">{u.email}</span>
                    </Link>
                  </td>
                  <td className="px-6 py-3">
                    <div className="flex items-center gap-2">
                      {u.channel_thumbnail_url && (
                        <img src={u.channel_thumbnail_url} alt="" className="w-7 h-7 rounded-full object-cover" />
                      )}
                      <span className="text-on-surface">{u.channel_title || "—"}</span>
                      {u.has_fetch_error && (
                        <span
                          title="Last channel refresh failed"
                          className="material-symbols-outlined text-[18px] text-error"
                          aria-label="Channel fetch error"
                        >
                          sync_problem
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-6 py-3 text-right tabular-nums">{formatCompact(u.subscribers)}</td>
                  <td className="px-6 py-3 text-right tabular-nums">
                    {u.prediction_count}
                    {u.last_prediction_at && (
                      <span className="block text-on-surface-variant text-sm">
                        {formatRelativeTime(u.last_prediction_at)}
                      </span>
                    )}
                  </td>
                  <td className="px-6 py-3 whitespace-nowrap">{formatDate(u.created_at)}</td>
                  <td className="px-6 py-3">
                    <StatusPill active={u.is_active} />
                  </td>
                </tr>
              ))}
              {!result &&
                loading &&
                Array.from({ length: 6 }, (_, i) => (
                  <tr key={i} className="border-t border-outline-variant/30">
                    <td colSpan={6} className="px-6 py-3">
                      <div className="h-9 rounded-lg bg-surface-container-high/60 shimmer" />
                    </td>
                  </tr>
                ))}
              {result && result.items.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-6 py-10 text-center text-on-surface-variant">
                    No users match.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {result && <Pagination total={result.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} />}
    </AdminLayout>
  );
}
