import { useEffect, useRef, useState } from "react";
import * as api from "../lib/api";
import { formatRelativeTime } from "../lib/format";

const TYPE_ICONS = {
  welcome: "celebration",
  prediction_complete: "query_stats",
  channel_fetch_success: "smart_display",
  channel_fetch_error: "error",
};

export default function NotificationsMenu() {
  const [open, setOpen] = useState(false);
  const [notifications, setNotifications] = useState([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const containerRef = useRef(null);

  function load() {
    setLoading(true);
    api
      .listNotifications()
      .then((data) => {
        setNotifications(data.notifications);
        setUnreadCount(data.unread_count);
      })
      .catch(() => {
        // Silent — the bell just won't show a badge if this fails.
      })
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    function handleClickOutside(e) {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  function toggleOpen() {
    setOpen((v) => {
      if (!v) load(); // refresh on open in case new ones arrived
      return !v;
    });
  }

  async function handleMarkAllRead() {
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
    setUnreadCount(0);
    try {
      await api.markAllNotificationsRead();
    } catch {
      load(); // reconcile with the server if that failed
    }
  }

  async function handleNotificationClick(notification) {
    if (notification.read) return;
    setNotifications((prev) =>
      prev.map((n) => (n.id === notification.id ? { ...n, read: true } : n)),
    );
    setUnreadCount((c) => Math.max(0, c - 1));
    try {
      await api.markNotificationRead(notification.id);
    } catch {
      load();
    }
  }

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={toggleOpen}
        className="relative p-2 text-on-surface-variant hover:bg-surface-container-high/50 rounded-full transition-colors active:scale-95"
        aria-label="Notifications"
      >
        <span className="material-symbols-outlined">notifications</span>
        {unreadCount > 0 && (
          <span className="absolute top-1 right-1 min-w-[16px] h-4 px-1 rounded-full bg-secondary text-white text-[10px] font-bold leading-4 text-center">
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-96 max-h-[28rem] overflow-y-auto glass-panel rounded-xl shadow-[0_20px_40px_rgba(0,0,0,0.15)] z-50">
          <div className="flex items-center justify-between px-4 py-3 border-b border-outline-variant/30 sticky top-0 bg-surface-container-lowest/90 backdrop-blur">
            <span className="font-headline-md text-headline-md text-on-background">Notifications</span>
            {unreadCount > 0 && (
              <button
                type="button"
                onClick={handleMarkAllRead}
                className="font-label-sm text-label-sm text-primary hover:text-secondary transition-colors"
              >
                Mark all read
              </button>
            )}
          </div>

          {loading && notifications.length === 0 && (
            <div className="p-6 text-center text-on-surface-variant font-body-md text-body-md">
              Loading…
            </div>
          )}

          {!loading && notifications.length === 0 && (
            <div className="p-8 flex flex-col items-center gap-2 text-center">
              <span className="material-symbols-outlined text-3xl text-on-surface-variant">
                notifications_off
              </span>
              <p className="font-body-md text-body-md text-on-surface-variant">
                Nothing yet — we'll let you know when something happens.
              </p>
            </div>
          )}

          <ul>
            {notifications.map((n) => (
              <li key={n.id}>
                <button
                  type="button"
                  onClick={() => handleNotificationClick(n)}
                  className={`w-full text-left px-4 py-3 flex gap-3 items-start hover:bg-surface-container-high/40 transition-colors border-b border-outline-variant/20 last:border-b-0 ${
                    n.read ? "" : "bg-primary-fixed/10"
                  }`}
                >
                  <span
                    className={`material-symbols-outlined text-[20px] mt-0.5 ${
                      n.type === "channel_fetch_error" ? "text-error" : "text-primary"
                    }`}
                  >
                    {TYPE_ICONS[n.type] || "info"}
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-label-md text-label-md text-on-background">{n.title}</span>
                      {!n.read && <span className="w-1.5 h-1.5 rounded-full bg-secondary flex-shrink-0" />}
                    </div>
                    <p className="font-body-md text-body-md text-on-surface-variant text-sm mt-0.5 line-clamp-2">
                      {n.message}
                    </p>
                    <p className="font-label-sm text-label-sm text-outline mt-1">
                      {formatRelativeTime(n.created_at)}
                    </p>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
