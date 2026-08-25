import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import Sidebar from "../components/Sidebar";
import Topbar from "../components/Topbar";
import { useAuth } from "../context/AuthContext";
import * as api from "../lib/api";
import { formatCompact } from "../lib/format";

export default function Dashboard() {
  const { user } = useAuth();
  const [summary, setSummary] = useState(null);
  const [predictions, setPredictions] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    Promise.all([api.getDashboardSummary(), api.listPredictions()])
      .then(([summaryData, predictionsData]) => {
        if (cancelled) return;
        setSummary(summaryData);
        setPredictions(predictionsData);
      })
      .catch(() => {
        // RequireAuth + the AuthContext's /auth/me check already guard the
        // common failure case (an expired token); a transient fetch error
        // here just leaves the dashboard showing its loading/empty state.
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const displayName = summary?.full_name || user?.full_name || "";
  const firstName = displayName.split(" ")[0] || displayName;

  return (
    <div className="bg-background text-on-background min-h-screen font-body-md selection:bg-primary-fixed selection:text-on-primary-fixed">
      <Sidebar active="dashboard" />
      <Topbar />

      <main className="ml-64 pt-24 px-margin-desktop pb-24 max-w-container-max mx-auto flex flex-col gap-[64px]">
        <section className="flex flex-col gap-6 md:flex-row md:items-end md:justify-between">
          <div>
            <p className="font-label-md text-label-md text-primary mb-2 uppercase tracking-widest">
              Dashboard Overview
            </p>
            <h2 className="font-display-lg text-display-lg text-on-background mb-4">
              Welcome back,{" "}
              <span className="bg-gradient-to-r from-primary to-secondary bg-clip-text text-transparent">
                {loading ? "…" : firstName || "Creator"}
              </span>
            </h2>
            <div className="flex items-center gap-6 text-on-surface-variant">
              <div className="flex items-center gap-2">
                <span className="material-symbols-outlined fill text-primary">group</span>
                <span className="font-headline-md text-headline-md">
                  {summary ? formatCompact(summary.subscribers) : "—"}
                </span>
                <span className="font-body-md text-body-md text-tertiary">Subscribers</span>
              </div>
              <div className="w-px h-8 bg-outline-variant" />
              <div className="flex items-center gap-2">
                <span className="material-symbols-outlined fill text-secondary">visibility</span>
                <span className="font-headline-md text-headline-md">
                  {summary ? formatCompact(summary.monthly_views) : "—"}
                </span>
                <span className="font-body-md text-body-md text-tertiary">Monthly Views</span>
              </div>
            </div>
          </div>

          <Link
            to="/new-prediction"
            className="glass-panel p-8 rounded-[24px] shadow-[0_10px_30px_rgba(0,0,0,0.04)] hover:shadow-[0_20px_40px_rgba(0,0,0,0.08)] hover:scale-[1.01] transition-all cursor-pointer group w-full md:w-[400px] relative overflow-hidden block"
          >
            <div className="absolute inset-0 bg-gradient-to-br from-primary-fixed/40 to-secondary-fixed/40 z-0" />
            <div className="absolute -right-10 -top-10 w-32 h-32 bg-primary blur-3xl opacity-20 rounded-full" />
            <div className="relative z-10 flex flex-col items-start gap-4">
              <div className="w-12 h-12 bg-white rounded-2xl flex items-center justify-center shadow-sm text-primary group-hover:scale-110 transition-transform">
                <span className="material-symbols-outlined">auto_awesome</span>
              </div>
              <div>
                <h3 className="font-headline-lg text-headline-lg text-on-background mb-2">
                  Analyze New Content
                </h3>
                <p className="font-body-md text-body-md text-on-surface-variant">
                  Upload a thumbnail or title to predict performance before publishing.
                </p>
              </div>
              <span className="mt-4 bg-primary text-white px-6 py-3 rounded-full font-label-md text-label-md flex items-center gap-2 group-hover:bg-on-primary-fixed-variant transition-colors">
                Start Prediction
                <span className="material-symbols-outlined text-sm">arrow_forward</span>
              </span>
            </div>
          </Link>
        </section>

        <section className="flex flex-col gap-6">
          <div className="flex items-center justify-between">
            <h3 className="font-headline-md text-headline-md text-on-background">Recent Predictions</h3>
            {predictions.length > 0 && (
              <Link
                to="/new-prediction"
                className="font-label-md text-label-md text-primary hover:text-secondary transition-colors"
              >
                New prediction
              </Link>
            )}
          </div>

          {!loading && predictions.length === 0 && (
            <div className="glass-panel rounded-xl p-10 flex flex-col items-center text-center gap-3">
              <span className="material-symbols-outlined text-4xl text-primary">query_stats</span>
              <p className="font-body-md text-body-md text-on-surface-variant">
                No predictions yet — analyze your first piece of content to see it here.
              </p>
              <Link
                to="/new-prediction"
                className="mt-2 bg-primary text-white px-5 py-2.5 rounded-full font-label-md text-label-md"
              >
                Create a prediction
              </Link>
            </div>
          )}

          {predictions.length > 0 && (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
              {predictions.map((p) => (
                <Link
                  key={p.id}
                  to={p.status === "complete" ? `/prediction-result/${p.id}` : "/new-prediction"}
                  className="glass-panel rounded-xl overflow-hidden hover:shadow-[0_20px_40px_rgba(0,0,0,0.08)] hover:scale-[1.01] transition-all"
                >
                  <div className="aspect-video bg-surface-container-highest overflow-hidden">
                    {p.thumbnail_url ? (
                      <img
                        src={api.fileUrl(p.thumbnail_url)}
                        alt={p.title}
                        className="w-full h-full object-cover"
                      />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center text-on-surface-variant">
                        <span className="material-symbols-outlined text-3xl">image</span>
                      </div>
                    )}
                  </div>
                  <div className="p-4">
                    <div className="flex items-center gap-2 mb-1">
                      {p.category && (
                        <span className="px-2 py-0.5 bg-primary-fixed/30 text-on-primary-fixed-variant rounded-full font-label-sm text-label-sm">
                          {p.category}
                        </span>
                      )}
                      {p.status === "draft" && (
                        <span className="px-2 py-0.5 bg-surface-container-highest text-on-surface-variant rounded-full font-label-sm text-label-sm">
                          Draft
                        </span>
                      )}
                    </div>
                    <h4 className="font-label-md text-label-md text-on-background line-clamp-1">{p.title}</h4>
                    {p.predicted_views != null && (
                      <p className="font-body-md text-body-md text-tertiary mt-1">
                        {formatCompact(p.predicted_views)} predicted views
                      </p>
                    )}
                  </div>
                </Link>
              ))}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
