import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import AdminLayout, { ErrorBanner, LoadingBlocks, StatusPill } from "../../components/admin/AdminLayout";
import { actionLabel, formatDate, formatDateTime } from "../../lib/adminFormat";
import { useAdminAuth } from "../../context/AdminAuthContext";
import * as adminApi from "../../lib/adminApi";
import { formatCompact, modelLabel } from "../../lib/format";

const buttonClass =
  "flex items-center gap-2 px-4 py-2 rounded-lg border border-outline-variant font-label-md text-label-md text-on-surface hover:bg-surface-container-high disabled:opacity-50 disabled:cursor-not-allowed transition-colors";

export default function AdminUserDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { errorMessage } = useAdminAuth();
  const [user, setUser] = useState(null);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(null);
  const [confirming, setConfirming] = useState(null); // "disable" | "delete"
  const [confirmEmail, setConfirmEmail] = useState("");

  useEffect(() => {
    let cancelled = false;
    adminApi
      .getUser(id)
      .then((data) => !cancelled && setUser(data))
      .catch((err) => !cancelled && setError(errorMessage(err, "Couldn't load this user.")));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  async function run(action, call, successMessage) {
    setBusy(action);
    setError(null);
    setNotice(null);
    try {
      const updated = await call();
      setUser(updated);
      setNotice(typeof successMessage === "function" ? successMessage(updated) : successMessage);
      setConfirming(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function handleDelete() {
    setBusy("delete");
    setError(null);
    try {
      await adminApi.deleteUser(user.id, confirmEmail);
      navigate("/admin/users", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
      setBusy(null);
    }
  }

  if (!user) {
    return (
      <AdminLayout active="users" title="User">
        <ErrorBanner message={error} />
        {!error && <LoadingBlocks tiles={0} panels={2} />}
      </AdminLayout>
    );
  }

  const channel = user.channel;

  return (
    <AdminLayout
      active="users"
      title={user.full_name}
      subtitle={user.email}
      actions={
        <Link to="/admin/users" className="font-label-md text-label-md text-primary flex items-center gap-1">
          <span className="material-symbols-outlined text-[18px]">arrow_back</span>
          All users
        </Link>
      }
    >
      <ErrorBanner message={error} />
      {notice && (
        <div className="rounded-lg bg-primary-fixed/40 text-on-primary-fixed px-4 py-3 font-body-md text-body-md text-sm">
          {notice}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <section className="glass-panel rounded-2xl p-6 flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <h3 className="font-headline-md text-headline-md text-on-background">Account</h3>
            <StatusPill active={user.is_active} />
          </div>
          <dl className="grid grid-cols-2 gap-y-3 font-body-md text-body-md">
            <Field label="User ID" value={`#${user.id}`} />
            <Field label="Joined" value={formatDate(user.created_at)} />
            <Field label="Subscribers" value={formatCompact(user.subscribers)} />
            <Field label="Monthly views" value={formatCompact(user.monthly_views)} />
            <Field label="Predictions" value={`${user.complete_count} complete · ${user.draft_count} drafts`} />
            <Field label="Notifications" value={`${user.notification_count} (${user.unread_notification_count} unread)`} />
          </dl>
        </section>

        <section className="glass-panel rounded-2xl p-6 flex flex-col gap-4 lg:col-span-2">
          <h3 className="font-headline-md text-headline-md text-on-background">Linked channel</h3>
          {channel.channel_id ? (
            <div className="flex items-start gap-4">
              {channel.thumbnail_url && (
                <img src={channel.thumbnail_url} alt="" className="w-14 h-14 rounded-full object-cover" />
              )}
              <div className="flex-1 min-w-0">
                <p className="font-label-md text-label-md text-on-surface">{channel.title}</p>
                <a
                  href={channel.channel_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-primary text-sm break-all hover:underline"
                >
                  {channel.channel_url}
                </a>
                <dl className="grid grid-cols-2 sm:grid-cols-4 gap-y-3 mt-3 font-body-md text-body-md">
                  <Field
                    label="Subscribers"
                    value={channel.subscriber_hidden ? "Hidden" : formatCompact(channel.subscriber_count)}
                  />
                  <Field label="Views" value={formatCompact(channel.view_count)} />
                  <Field label="Videos" value={formatCompact(channel.video_count)} />
                  <Field label="Fetched" value={formatDateTime(channel.fetched_at)} />
                </dl>
              </div>
            </div>
          ) : (
            <p className="text-on-surface-variant">
              No channel data yet{channel.channel_url ? ` for ${channel.channel_url}` : ""}.
            </p>
          )}
          {channel.fetch_error && (
            <div className="flex items-start gap-2 rounded-lg bg-error-container/50 text-on-error-container px-4 py-3 text-sm">
              <span className="material-symbols-outlined text-[18px]">sync_problem</span>
              <span>Last refresh failed: {channel.fetch_error}</span>
            </div>
          )}
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              className={buttonClass}
              disabled={!channel.channel_url || busy !== null}
              onClick={() =>
                run("refresh", () => adminApi.refreshUserChannel(user.id), (u) =>
                  u.channel.fetch_error ? "Refresh failed — see the error above." : "Channel refreshed.",
                )
              }
            >
              <span className="material-symbols-outlined text-[18px]">refresh</span>
              {busy === "refresh" ? "Refreshing…" : "Refresh channel"}
            </button>
            {channel.fetch_error && (
              <button
                type="button"
                className={buttonClass}
                disabled={busy !== null}
                onClick={() => run("clear", () => adminApi.clearUserFetchError(user.id), "Fetch error cleared.")}
              >
                <span className="material-symbols-outlined text-[18px]">done_all</span>
                Clear error
              </button>
            )}
          </div>
        </section>
      </div>

      <section className="glass-panel rounded-2xl p-6 flex flex-col gap-4">
        <h3 className="font-headline-md text-headline-md text-on-background">Access</h3>
        <div className="flex flex-wrap gap-3 items-center">
          {user.is_active ? (
            <button
              type="button"
              className={buttonClass}
              disabled={busy !== null}
              onClick={() => setConfirming(confirming === "disable" ? null : "disable")}
            >
              <span className="material-symbols-outlined text-[18px]">block</span>
              Disable account
            </button>
          ) : (
            <button
              type="button"
              className={buttonClass}
              disabled={busy !== null}
              onClick={() => run("enable", () => adminApi.enableUser(user.id), "Account enabled.")}
            >
              <span className="material-symbols-outlined text-[18px]">check_circle</span>
              {busy === "enable" ? "Enabling…" : "Enable account"}
            </button>
          )}
          <button
            type="button"
            className={`${buttonClass} text-error border-error/50 hover:bg-error-container/40`}
            disabled={busy !== null}
            onClick={() => {
              setConfirmEmail("");
              setConfirming(confirming === "delete" ? null : "delete");
            }}
          >
            <span className="material-symbols-outlined text-[18px]">delete</span>
            Delete account
          </button>
        </div>

        {confirming === "disable" && (
          <ConfirmBox>
            <p>
              {user.full_name} will be signed out everywhere and can&apos;t log in until you enable the account again.
              Their data is kept.
            </p>
            <div className="flex gap-3">
              <button
                type="button"
                className={buttonClass}
                disabled={busy !== null}
                onClick={() => run("disable", () => adminApi.disableUser(user.id), "Account disabled.")}
              >
                {busy === "disable" ? "Disabling…" : "Yes, disable"}
              </button>
              <button type="button" className={buttonClass} onClick={() => setConfirming(null)}>
                Cancel
              </button>
            </div>
          </ConfirmBox>
        )}

        {confirming === "delete" && (
          <ConfirmBox>
            <p>
              This permanently deletes the account, its {user.prediction_count} prediction
              {user.prediction_count === 1 ? "" : "s"}, uploaded files and notifications. It can&apos;t be undone.
              Type <strong className="font-label-md">{user.email}</strong> to confirm.
            </p>
            <input
              className="w-full max-w-md bg-surface-container-lowest border border-outline-variant rounded-lg px-4 py-2 font-body-md text-body-md input-focus-ring"
              value={confirmEmail}
              onChange={(e) => setConfirmEmail(e.target.value)}
              placeholder={user.email}
              aria-label="Type the user's email to confirm"
            />
            <div className="flex gap-3">
              <button
                type="button"
                className="px-4 py-2 rounded-lg bg-error text-on-error font-label-md text-label-md disabled:opacity-50 disabled:cursor-not-allowed"
                disabled={confirmEmail.trim() !== user.email || busy !== null}
                onClick={handleDelete}
              >
                {busy === "delete" ? "Deleting…" : "Delete permanently"}
              </button>
              <button type="button" className={buttonClass} onClick={() => setConfirming(null)}>
                Cancel
              </button>
            </div>
          </ConfirmBox>
        )}
      </section>

      <section className="glass-panel rounded-2xl overflow-hidden">
        <h3 className="font-headline-md text-headline-md text-on-background px-6 pt-6 pb-3">Recent predictions</h3>
        {user.recent_predictions.length === 0 ? (
          <p className="px-6 pb-6 text-on-surface-variant">No predictions yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left font-body-md text-body-md">
              <thead className="text-on-surface-variant font-label-md text-label-md border-b border-outline-variant/50">
                <tr>
                  <th className="px-6 py-3">Title</th>
                  <th className="px-6 py-3">Category</th>
                  <th className="px-6 py-3">Status</th>
                  <th className="px-6 py-3 text-right">Predicted views</th>
                  <th className="px-6 py-3">Model</th>
                  <th className="px-6 py-3">Created</th>
                </tr>
              </thead>
              <tbody>
                {user.recent_predictions.map((p) => (
                  <tr key={p.id} className="border-t border-outline-variant/30">
                    <td className="px-6 py-3 text-on-surface">{p.title}</td>
                    <td className="px-6 py-3">{p.category || "—"}</td>
                    <td className="px-6 py-3 capitalize">{p.status}</td>
                    <td className="px-6 py-3 text-right tabular-nums">{formatCompact(p.predicted_views)}</td>
                    <td className="px-6 py-3 text-sm text-on-surface-variant">{modelLabel(p) || "—"}</td>
                    <td className="px-6 py-3 whitespace-nowrap">{formatDateTime(p.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="glass-panel rounded-2xl p-6 flex flex-col gap-3">
        <h3 className="font-headline-md text-headline-md text-on-background">Admin activity on this account</h3>
        {user.recent_activity.length === 0 ? (
          <p className="text-on-surface-variant">No admin actions yet.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {user.recent_activity.map((a) => (
              <li key={a.id} className="flex flex-wrap gap-x-3 font-body-md text-body-md">
                <span className="text-on-surface-variant whitespace-nowrap">{formatDateTime(a.created_at)}</span>
                <span className="text-on-surface">{actionLabel(a.action)}</span>
                <span className="text-on-surface-variant">by {a.admin_email || "a removed admin"}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </AdminLayout>
  );
}

function Field({ label, value }) {
  return (
    <div>
      <dt className="font-label-sm text-label-sm text-on-surface-variant">{label}</dt>
      <dd className="text-on-surface">{value}</dd>
    </div>
  );
}

function ConfirmBox({ children }) {
  return (
    <div className="rounded-xl border border-error/40 bg-error-container/20 p-4 flex flex-col gap-3 font-body-md text-body-md text-on-surface">
      {children}
    </div>
  );
}
