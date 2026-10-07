import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import AdminLayout, { ErrorBanner, Pagination } from "../../components/admin/AdminLayout";
import { AUDIT_ACTIONS, actionLabel, formatDateTime } from "../../lib/adminFormat";
import { useAdminAuth } from "../../context/AdminAuthContext";
import * as adminApi from "../../lib/adminApi";

const PAGE_SIZE = 50;

function describeTarget(entry) {
  if (entry.target_type === "channel") {
    if (entry.details?.channel_id) return <span className="font-mono text-sm">{entry.details.channel_id}</span>;
    if (entry.details?.count != null) return <span>{entry.details.count} channels</span>;
    return null;
  }
  if (entry.target_type === "prediction") {
    return (
      <span>
        &ldquo;{entry.details?.title}&rdquo;{" "}
        <span className="text-on-surface-variant text-sm">({entry.details?.user_email})</span>
      </span>
    );
  }
  if (entry.target_type !== "user" || entry.target_id == null) return null;
  const label = entry.details?.email || `user #${entry.target_id}`;
  // Deleted users have no page left to open.
  if (entry.action === "user.delete") return <span>{label}</span>;
  return (
    <Link to={`/admin/users/${entry.target_id}`} className="text-primary hover:underline">
      {label}
    </Link>
  );
}

export default function AdminActivity() {
  const { errorMessage } = useAdminAuth();
  const [action, setAction] = useState("");
  const [offset, setOffset] = useState(0);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    adminApi
      .listAuditLog({ action, limit: PAGE_SIZE, offset })
      .then((data) => {
        if (!cancelled) {
          setResult(data);
          setError(null);
        }
      })
      .catch((err) => !cancelled && setError(errorMessage(err, "Couldn't load the activity log.")));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [action, offset]);

  return (
    <AdminLayout
      active="activity"
      title="Activity log"
      subtitle="Every admin sign-in and change, newest first."
      actions={
        <select
          className="bg-surface-container-low border border-outline-variant rounded-lg px-3 py-2 font-body-md text-body-md text-on-surface"
          value={action}
          onChange={(e) => {
            setAction(e.target.value);
            setOffset(0);
          }}
          aria-label="Filter by action"
        >
          <option value="">All actions</option>
          {AUDIT_ACTIONS.map((a) => (
            <option key={a} value={a}>
              {actionLabel(a)}
            </option>
          ))}
        </select>
      }
    >
      <ErrorBanner message={error} />

      <section className="glass-panel rounded-2xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left font-body-md text-body-md">
            <thead className="text-on-surface-variant font-label-md text-label-md border-b border-outline-variant/50">
              <tr>
                <th className="px-6 py-3">When</th>
                <th className="px-6 py-3">Admin</th>
                <th className="px-6 py-3">Action</th>
                <th className="px-6 py-3">Target</th>
                <th className="px-6 py-3">Details</th>
              </tr>
            </thead>
            <tbody>
              {result?.items.map((e) => (
                <tr key={e.id} className="border-t border-outline-variant/30 align-top">
                  <td className="px-6 py-3 whitespace-nowrap">{formatDateTime(e.created_at)}</td>
                  <td className="px-6 py-3">{e.admin_email || "Removed admin"}</td>
                  <td className="px-6 py-3 text-on-surface">{actionLabel(e.action)}</td>
                  <td className="px-6 py-3">{describeTarget(e) || "—"}</td>
                  <td className="px-6 py-3 text-sm text-on-surface-variant">
                    {e.action === "user.refresh_channel"
                      ? e.details.ok
                        ? "Succeeded"
                        : `Failed: ${e.details.error}`
                      : e.action === "user.clear_fetch_error"
                        ? e.details.error
                        : "—"}
                  </td>
                </tr>
              ))}
              {result && result.items.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-6 py-10 text-center text-on-surface-variant">
                    Nothing logged yet.
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
