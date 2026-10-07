import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import AdminLayout, { ErrorBanner, LoadingBlocks } from "../../components/admin/AdminLayout";
import SeriesChart from "../../components/admin/SeriesChart";
import { useAdminAuth } from "../../context/AdminAuthContext";
import * as adminApi from "../../lib/adminApi";
import { fileUrl } from "../../lib/api";
import { formatDateTime } from "../../lib/adminFormat";
import { formatCompact, formatSignedPercent, modelLabel } from "../../lib/format";

export default function AdminPredictionDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { errorMessage } = useAdminAuth();
  const [prediction, setPrediction] = useState(null);
  const [error, setError] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    adminApi
      .getAdminPrediction(id)
      .then((data) => !cancelled && setPrediction(data))
      .catch((err) => !cancelled && setError(errorMessage(err, "Couldn't load this prediction.")));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const curve = useMemo(
    () =>
      prediction && {
        labels: prediction.trajectory.map((p) => `Day ${p.day}`),
        values: prediction.trajectory.map((p) => p.views),
      },
    [prediction],
  );

  async function handleDelete() {
    setDeleting(true);
    setError(null);
    try {
      await adminApi.deleteAdminPrediction(prediction.id);
      navigate("/admin/predictions", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
      setDeleting(false);
    }
  }

  const back = (
    <Link to="/admin/predictions" className="font-label-md text-label-md text-primary flex items-center gap-1">
      <span className="material-symbols-outlined text-[18px]">arrow_back</span>
      All predictions
    </Link>
  );

  if (!prediction) {
    return (
      <AdminLayout active="predictions" title="Prediction" actions={back}>
        <ErrorBanner message={error} />
        {!error && <LoadingBlocks tiles={0} panels={1} />}
      </AdminLayout>
    );
  }

  const p = prediction;
  return (
    <AdminLayout active="predictions" title={p.title} subtitle={`Prediction #${p.id}`} actions={back}>
      <ErrorBanner message={error} />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <section className="glass-panel rounded-2xl p-6 flex flex-col gap-4">
          {p.thumbnail_url ? (
            <img src={fileUrl(p.thumbnail_url)} alt="Thumbnail" className="w-full aspect-video rounded-lg object-cover" />
          ) : (
            <div className="w-full aspect-video rounded-lg bg-surface-container-high flex items-center justify-center text-on-surface-variant">
              No thumbnail
            </div>
          )}
          <dl className="grid grid-cols-2 gap-y-3 font-body-md text-body-md">
            <Field label="Owner">
              <Link to={`/admin/users/${p.user_id}`} className="text-primary hover:underline">
                {p.user_name}
              </Link>
              <span className="block text-sm text-on-surface-variant break-all">{p.user_email}</span>
            </Field>
            <Field label="Status">
              <span className="capitalize">{p.status}</span>
            </Field>
            <Field label="Category">{p.category || "—"}</Field>
            <Field label="Created">{formatDateTime(p.created_at)}</Field>
            <Field label="Planned upload">
              {p.target_date ? `${p.target_date}${p.target_time ? ` ${p.target_time}` : ""}` : "—"}
            </Field>
            <Field label="Tags">{p.tags.length ? p.tags.join(", ") : "—"}</Field>
          </dl>
          {p.dataset_url && (
            <a href={fileUrl(p.dataset_url)} className="text-primary text-sm hover:underline" target="_blank" rel="noreferrer">
              Download the attached dataset
            </a>
          )}
        </section>

        <section className="glass-panel rounded-2xl p-6 flex flex-col gap-4 lg:col-span-2">
          <h3 className="font-headline-md text-headline-md text-on-background">Forecast</h3>
          {p.status === "draft" ? (
            <p className="text-on-surface-variant">This is a draft, so the model was never run.</p>
          ) : (
            <>
              <dl className="grid grid-cols-2 sm:grid-cols-3 gap-y-3 font-body-md text-body-md">
                <Field label="Predicted views (7 days)">{formatCompact(p.predicted_views)}</Field>
                <Field label="Confidence">{p.confidence == null ? "—" : `${Math.round(p.confidence * 100)}%`}</Field>
                <Field label="vs. channel average">{formatSignedPercent(p.change_vs_avg)}</Field>
                <Field label="Model">{modelLabel(p) || "—"}</Field>
                <Field label="v∞ (long-run views)">{p.v_inf == null ? "—" : formatCompact(p.v_inf)}</Field>
                <Field label="τ (growth time)">{p.tau == null ? "—" : p.tau.toFixed(2)}</Field>
              </dl>
              {curve.values.length > 0 ? (
                <div>
                  <p className="font-label-md text-label-md text-on-surface-variant mb-2">Cumulative views by day</p>
                  <SeriesChart type="line" labels={curve.labels} values={curve.values} label="Views" formatValue={formatCompact} height={260} />
                </div>
              ) : (
                <p className="text-on-surface-variant">No trajectory was saved for this prediction.</p>
              )}
            </>
          )}
        </section>
      </div>

      <section className="glass-panel rounded-2xl p-6 flex flex-col gap-3">
        <h3 className="font-headline-md text-headline-md text-on-background">Remove</h3>
        {!confirming ? (
          <button
            type="button"
            onClick={() => setConfirming(true)}
            className="self-start flex items-center gap-2 px-4 py-2 rounded-lg border border-error/50 text-error font-label-md text-label-md hover:bg-error-container/40"
          >
            <span className="material-symbols-outlined text-[18px]">delete</span>
            Delete prediction
          </button>
        ) : (
          <div className="rounded-xl border border-error/40 bg-error-container/20 p-4 flex flex-col gap-3 text-on-surface">
            <p>
              This deletes the prediction and its uploaded files for {p.user_name}. It can&apos;t be undone.
            </p>
            <div className="flex gap-3">
              <button
                type="button"
                onClick={handleDelete}
                disabled={deleting}
                className="px-4 py-2 rounded-lg bg-error text-on-error font-label-md text-label-md disabled:opacity-50"
              >
                {deleting ? "Deleting…" : "Delete permanently"}
              </button>
              <button
                type="button"
                onClick={() => setConfirming(false)}
                className="px-4 py-2 rounded-lg border border-outline-variant font-label-md text-label-md"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </section>
    </AdminLayout>
  );
}

function Field({ label, children }) {
  return (
    <div>
      <dt className="font-label-sm text-label-sm text-on-surface-variant">{label}</dt>
      <dd className="text-on-surface">{children}</dd>
    </div>
  );
}
