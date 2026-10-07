import { Link, useNavigate } from "react-router-dom";
import ThemeToggle from "../ThemeToggle";
import { useAdminAuth } from "../../context/AdminAuthContext";

const navItems = [
  { key: "overview", label: "Overview", icon: "space_dashboard", to: "/admin" },
  { key: "users", label: "Users", icon: "group", to: "/admin/users" },
  { key: "predictions", label: "Predictions", icon: "query_stats", to: "/admin/predictions" },
  { key: "cache", label: "Channel cache", icon: "cached", to: "/admin/cache" },
  { key: "activity", label: "Activity log", icon: "history", to: "/admin/activity" },
];

export default function AdminLayout({ active, title, subtitle, actions, children }) {
  const navigate = useNavigate();
  const { admin, logout } = useAdminAuth();

  function handleLogout() {
    logout();
    navigate("/admin/login", { replace: true });
  }

  return (
    <div className="bg-background text-on-background min-h-screen font-body-md">
      <aside className="fixed left-0 top-0 h-full w-64 z-50 bg-surface-container-lowest/70 dark:bg-surface-container-low/70 backdrop-blur-2xl shadow-[10px_0_30px_rgba(0,0,0,0.04)] flex flex-col gap-unit p-6">
        <Link to="/admin" className="flex items-center gap-3 mb-10 mt-2 hover:opacity-80 transition-opacity">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-primary to-secondary flex items-center justify-center text-white shadow-lg">
            <span className="material-symbols-outlined fill">admin_panel_settings</span>
          </div>
          <div>
            <h1 className="font-headline-md text-headline-md font-black bg-gradient-to-r from-primary to-secondary bg-clip-text text-transparent">
              TrendCast
            </h1>
            <p className="font-label-sm text-label-sm text-on-surface-variant">Admin console</p>
          </div>
        </Link>

        <nav className="flex flex-col gap-2 flex-grow">
          {navItems.map((item) => {
            const isActive = item.key === active;
            return (
              <Link
                key={item.key}
                to={item.to}
                className={
                  isActive
                    ? "flex items-center gap-3 px-4 py-3 text-primary font-bold bg-primary-fixed/20 rounded-xl transition-all"
                    : "flex items-center gap-3 px-4 py-3 text-on-surface-variant hover:bg-surface-container-high/80 transition-all rounded-xl"
                }
              >
                <span className={isActive ? "material-symbols-outlined fill" : "material-symbols-outlined"}>
                  {item.icon}
                </span>
                <span className="font-label-md text-label-md">{item.label}</span>
              </Link>
            );
          })}
        </nav>

        {admin && (
          <div className="px-4 py-2 border-t border-outline-variant/50 pt-4">
            <p className="font-label-md text-label-md text-on-surface truncate">{admin.full_name}</p>
            <p className="font-label-sm text-label-sm text-on-surface-variant truncate">{admin.email}</p>
          </div>
        )}
        <button
          type="button"
          onClick={handleLogout}
          className="flex items-center gap-3 px-4 py-3 text-on-surface-variant hover:bg-error-container/40 hover:text-error transition-all rounded-xl text-left"
        >
          <span className="material-symbols-outlined">logout</span>
          <span className="font-label-md text-label-md">Log out</span>
        </button>
      </aside>

      <header className="fixed top-0 right-0 w-full z-40 bg-surface/70 backdrop-blur-xl shadow-sm flex justify-end items-center h-16 px-margin-desktop">
        <div className="flex items-center gap-4">
          <span className="font-label-sm text-label-sm uppercase tracking-widest text-secondary bg-secondary-fixed/40 px-3 py-1 rounded-full">
            Admin
          </span>
          <ThemeToggle />
        </div>
      </header>

      <main className="ml-64 pt-24 px-margin-desktop pb-24 max-w-container-max mx-auto flex flex-col gap-8">
        <div className="flex items-end justify-between gap-4 flex-wrap">
          <div>
            <h2 className="font-headline-lg text-headline-lg text-on-background">{title}</h2>
            {subtitle && <p className="font-body-md text-body-md text-on-surface-variant mt-2">{subtitle}</p>}
          </div>
          {actions}
        </div>
        {children}
      </main>
    </div>
  );
}

export function ErrorBanner({ message }) {
  if (!message) return null;
  return (
    <div className="rounded-lg bg-error-container/60 text-on-error-container px-4 py-3 font-body-md text-body-md text-sm">
      {message}
    </div>
  );
}

export function StatTile({ icon, label, value, sub }) {
  return (
    <div className="glass-panel rounded-xl p-6 flex flex-col gap-2">
      <span className="material-symbols-outlined text-primary">{icon}</span>
      <span className="font-headline-lg text-headline-lg text-on-background">{value}</span>
      <span className="font-label-md text-label-md text-on-surface-variant">{label}</span>
      {sub && <span className="font-label-sm text-label-sm text-tertiary">{sub}</span>}
    </div>
  );
}

export function StatusPill({ active }) {
  return active ? (
    <span className="inline-flex items-center gap-1 font-label-sm text-label-sm text-on-surface bg-surface-container-high px-2.5 py-1 rounded-full">
      <span className="material-symbols-outlined text-[16px] text-primary">check_circle</span>
      Active
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 font-label-sm text-label-sm text-on-error-container bg-error-container/60 px-2.5 py-1 rounded-full">
      <span className="material-symbols-outlined text-[16px]">block</span>
      Disabled
    </span>
  );
}

export function Pagination({ total, limit, offset, onChange }) {
  if (total <= limit) return null;
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.ceil(total / limit);
  const button =
    "px-3 py-1.5 rounded-lg border border-outline-variant font-label-md text-label-md disabled:opacity-40 disabled:cursor-not-allowed hover:bg-surface-container-high";
  return (
    <div className="flex items-center justify-between font-body-md text-body-md text-on-surface-variant">
      <span>
        {offset + 1}–{Math.min(offset + limit, total)} of {total}
      </span>
      <div className="flex items-center gap-2">
        <button type="button" className={button} disabled={page <= 1} onClick={() => onChange(offset - limit)}>
          Previous
        </button>
        <span>
          Page {page} of {pages}
        </span>
        <button type="button" className={button} disabled={page >= pages} onClick={() => onChange(offset + limit)}>
          Next
        </button>
      </div>
    </div>
  );
}

// Placeholder blocks while a page's data loads (round trips to the database
// can take a few seconds), so the layout doesn't jump when it arrives.
export function LoadingBlocks({ tiles = 4, panels = 1 }) {
  return (
    <div className="flex flex-col gap-6" aria-busy="true" aria-label="Loading">
      {tiles > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
          {Array.from({ length: tiles }, (_, i) => (
            <div key={i} className="glass-panel rounded-xl h-[132px] shimmer" />
          ))}
        </div>
      )}
      {Array.from({ length: panels }, (_, i) => (
        <div key={i} className="glass-panel rounded-2xl h-[320px] shimmer" />
      ))}
    </div>
  );
}
