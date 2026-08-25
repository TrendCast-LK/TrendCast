import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

const navItems = [
  { key: "dashboard", label: "Dashboard", icon: "dashboard", to: "/dashboard" },
  { key: "predictions", label: "Predictions", icon: "query_stats", to: "/new-prediction" },
  { key: "trends", label: "Trends", icon: "trending_up", to: "/trends" },
  { key: "settings", label: "Settings", icon: "settings", to: "/settings" },
];

export default function Sidebar({ active = "dashboard" }) {
  const navigate = useNavigate();
  const { logout } = useAuth();

  function handleLogout() {
    logout();
    navigate("/", { replace: true });
  }

  return (
    <aside className="fixed left-0 top-0 h-full w-64 z-50 bg-surface-container-lowest/70 dark:bg-surface-container-low/70 backdrop-blur-2xl shadow-[10px_0_30px_rgba(0,0,0,0.04)] flex flex-col gap-unit p-6">
      <Link to="/dashboard" className="flex items-center gap-3 mb-10 mt-2 hover:opacity-80 transition-opacity">
        <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-primary to-secondary flex items-center justify-center text-white shadow-lg">
          <span className="material-symbols-outlined fill">insights</span>
        </div>
        <div>
          <h1 className="font-headline-md text-headline-md font-black bg-gradient-to-r from-primary to-secondary bg-clip-text text-transparent">
            ViewCast
          </h1>
          <p className="font-label-sm text-label-sm text-on-surface-variant">Predictive Brilliance</p>
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
                  ? "flex items-center gap-3 px-4 py-3 text-primary font-bold bg-primary-fixed/20 rounded-xl active:scale-98 duration-200 transition-all"
                  : "flex items-center gap-3 px-4 py-3 text-on-surface-variant hover:bg-surface-container-high/80 hover:scale-[1.01] transition-all rounded-xl active:scale-98 duration-200"
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

      <button
        type="button"
        onClick={handleLogout}
        className="flex items-center gap-3 px-4 py-3 text-on-surface-variant hover:bg-error-container/40 hover:text-error transition-all rounded-xl active:scale-98 duration-200 border-t border-outline-variant/50 mt-2 pt-4 text-left"
      >
        <span className="material-symbols-outlined">logout</span>
        <span className="font-label-md text-label-md">Log out</span>
      </button>

      <Link
        to="/new-prediction"
        className="btn-primary w-full py-3 rounded-xl font-label-md text-label-md font-bold flex items-center justify-center gap-2 transition-all hover:scale-[1.02] active:scale-95"
      >
        <span className="material-symbols-outlined">add_circle</span>
        New Prediction
      </Link>
    </aside>
  );
}
