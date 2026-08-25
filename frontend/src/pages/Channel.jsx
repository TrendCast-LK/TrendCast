import { useEffect, useState } from "react";
import Sidebar from "../components/Sidebar";
import Topbar from "../components/Topbar";
import * as api from "../lib/api";
import { ApiError } from "../lib/api";
import { formatCompact } from "../lib/format";

export default function Channel() {
  const [channel, setChannel] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);

  function load() {
    setLoading(true);
    setError(null);
    return api
      .getChannel()
      .then(setChannel)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Couldn't load channel data."))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load();
  }, []);

  async function handleRefresh() {
    setRefreshing(true);
    setError(null);
    try {
      const updated = await api.refreshChannel();
      setChannel(updated);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't refresh channel data.");
    } finally {
      setRefreshing(false);
    }
  }

  const notFetched = channel && !channel.channel_id;

  return (
    <div className="bg-background text-on-background min-h-screen font-body-md selection:bg-primary-fixed selection:text-on-primary-fixed">
      <Sidebar active="dashboard" />
      <Topbar searchPlaceholder="Search channel..." avatarUrl={channel?.thumbnail_url} />

      <main className="ml-64 pt-24 px-margin-desktop pb-24 max-w-container-max mx-auto flex flex-col gap-10">
        <div className="flex items-center justify-between">
          <div>
            <p className="font-label-md text-label-md text-primary mb-2 uppercase tracking-widest">
              Your Channel
            </p>
            <h2 className="font-headline-lg text-headline-lg text-on-background">Channel Data</h2>
          </div>
          <button
            type="button"
            onClick={handleRefresh}
            disabled={refreshing || loading}
            className="px-5 py-2.5 rounded-xl font-label-md text-label-md bg-surface-container text-on-surface hover:bg-surface-container-highest transition-colors disabled:opacity-60 disabled:cursor-not-allowed flex items-center gap-2"
          >
            <span className={`material-symbols-outlined text-[20px] ${refreshing ? "animate-spin" : ""}`}>
              refresh
            </span>
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>

        {loading && (
          <div className="flex items-center justify-center py-24">
            <span className="material-symbols-outlined animate-spin text-4xl text-primary">
              progress_activity
            </span>
          </div>
        )}

        {!loading && error && !channel && (
          <div className="rounded-lg bg-error-container/60 text-on-error-container px-4 py-3 font-body-md text-body-md text-sm">
            {error}
          </div>
        )}

        {!loading && channel && notFetched && (
          <div className="glass-panel rounded-xl p-10 flex flex-col items-center text-center gap-3">
            <span className="material-symbols-outlined text-4xl text-error">smart_display</span>
            <p className="font-body-md text-body-md text-on-background font-medium">
              We couldn't fetch this channel yet
            </p>
            <p className="font-body-md text-body-md text-on-surface-variant max-w-md">
              {channel.fetch_error || "Something went wrong fetching your channel data."}
            </p>
            <p className="font-label-sm text-label-sm text-outline">{channel.channel_url}</p>
            <button
              type="button"
              onClick={handleRefresh}
              disabled={refreshing}
              className="mt-2 bg-primary text-white px-5 py-2.5 rounded-full font-label-md text-label-md disabled:opacity-60"
            >
              Try again
            </button>
          </div>
        )}

        {!loading && channel && !notFetched && (
          <>
            {error && (
              <div className="rounded-lg bg-error-container/60 text-on-error-container px-4 py-3 font-body-md text-body-md text-sm">
                {error}
              </div>
            )}

            <section className="glass-panel rounded-2xl overflow-hidden shadow-[0_10px_30px_rgba(0,0,0,0.04)]">
              <div
                className="h-40 md:h-56 w-full bg-gradient-to-br from-primary to-secondary relative"
                style={
                  channel.banner_url
                    ? {
                        backgroundImage: `url('${channel.banner_url}')`,
                        backgroundSize: "cover",
                        backgroundPosition: "center",
                      }
                    : undefined
                }
              />
              <div className="px-6 md:px-8 pb-8">
                <div className="flex flex-col md:flex-row md:items-end gap-4 -mt-12">
                  <div className="w-24 h-24 rounded-full overflow-hidden border-4 border-surface-container-lowest bg-surface-container-lowest shadow-lg flex-shrink-0">
                    {channel.thumbnail_url ? (
                      <img
                        src={channel.thumbnail_url}
                        alt={channel.title}
                        className="w-full h-full object-cover"
                      />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center text-on-surface-variant">
                        <span className="material-symbols-outlined text-3xl">smart_display</span>
                      </div>
                    )}
                  </div>
                  <div className="flex-1 pt-2 md:pt-0">
                    <h3 className="font-headline-md text-headline-md text-on-background">{channel.title}</h3>
                    {channel.country && (
                      <p className="font-label-sm text-label-sm text-on-surface-variant mt-1">
                        {channel.country}
                        {channel.published_at &&
                          ` · Since ${new Date(channel.published_at).getFullYear()}`}
                      </p>
                    )}
                  </div>
                  <a
                    href={channel.channel_url}
                    target="_blank"
                    rel="noreferrer"
                    className="px-5 py-2.5 rounded-full font-label-md text-label-md bg-surface-container text-on-surface hover:bg-surface-container-highest transition-colors flex items-center gap-2 self-start md:self-end"
                  >
                    <span className="material-symbols-outlined text-[18px]">open_in_new</span>
                    View on YouTube
                  </a>
                </div>

                {channel.description && (
                  <p className="font-body-md text-body-md text-on-surface-variant mt-6 whitespace-pre-line line-clamp-4">
                    {channel.description}
                  </p>
                )}
              </div>
            </section>

            <section className="grid grid-cols-1 sm:grid-cols-3 gap-6">
              <StatTile
                icon="group"
                label="Subscribers"
                value={channel.subscriber_hidden ? "Hidden" : formatCompact(channel.subscriber_count)}
              />
              <StatTile icon="visibility" label="Total Views" value={formatCompact(channel.view_count)} />
              <StatTile
                icon="video_library"
                label="Videos"
                value={channel.video_count != null ? channel.video_count.toLocaleString() : "—"}
              />
            </section>

            {channel.fetched_at && (
              <p className="font-label-sm text-label-sm text-on-surface-variant text-right">
                Last updated {new Date(channel.fetched_at).toLocaleString()}
              </p>
            )}
          </>
        )}
      </main>
    </div>
  );
}

function StatTile({ icon, label, value }) {
  return (
    <div className="glass-panel rounded-xl p-6 flex flex-col gap-2">
      <span className="material-symbols-outlined text-primary">{icon}</span>
      <span className="font-headline-lg text-headline-lg text-on-background">{value}</span>
      <span className="font-label-md text-label-md text-on-surface-variant">{label}</span>
    </div>
  );
}
