import { Link } from "react-router-dom";
import ThemeToggle from "./ThemeToggle";
import NotificationsMenu from "./NotificationsMenu";
import HelpMenu from "./HelpMenu";
import { useAuth } from "../context/AuthContext";

const DEFAULT_AVATAR =
  "https://lh3.googleusercontent.com/aida-public/AB6AXuCFlsyHTqpgmCQZ1G2B8qT_x9A4BrkvDQzJ0ClRoCFwxp4NyfWdxWEMrZKpcA5UDh1sBxPqjF17Wlks1gibOoB5sil8bGwLdbsQk4pCQwnwEK2o7iP9tfscZa4YNLcevfHldg_OI_cEFrQ-ilF_-WeO3f1d_azMxyUP8Zouoiu67H5pqJc9f9M91_CVWo_PPVFt_9bmrukwZkdyrZpgWZ90nIXNHM6sB_M4UD8q0X1TZRlVwWqWZRk";

export default function ResultsTopbar() {
  const { user } = useAuth();

  return (
    <header className="fixed top-0 right-0 w-full z-40 bg-surface/70 backdrop-blur-xl shadow-sm hidden md:flex">
      <div className="flex justify-between items-center h-16 px-margin-desktop ml-64 w-full">
        <div className="flex items-center gap-4 w-1/3">
          <div className="relative w-full max-w-md">
            <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant/50">
              search
            </span>
            <input
              className="w-full bg-surface-container border border-outline-variant rounded-full py-2 pl-10 pr-4 text-body-md focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
              placeholder="Search predictions..."
              type="text"
            />
          </div>
        </div>
        <div className="flex items-center gap-8 justify-center w-1/3">
          <nav className="flex gap-6">
            <Link
              className="text-on-surface-variant hover:bg-surface-container-high/50 transition-colors active:scale-95 transition-transform px-3 py-2 rounded-lg"
              to="/dashboard"
            >
              Home
            </Link>
            <Link
              className="text-primary font-bold border-b-2 border-primary active:scale-95 transition-transform px-3 py-2"
              to="/new-prediction"
            >
              Predictions
            </Link>
          </nav>
        </div>
        <div className="flex items-center gap-4 justify-end w-1/3">
          <ThemeToggle />
          <NotificationsMenu />
          <HelpMenu />
          <Link
            to="/channel"
            title="Channel data"
            className="w-8 h-8 rounded-full overflow-hidden border border-outline-variant cursor-pointer hover:ring-2 hover:ring-primary transition-all block"
          >
            <img
              alt="Creator profile"
              className="w-full h-full object-cover"
              src={user?.channel_thumbnail_url || DEFAULT_AVATAR}
            />
          </Link>
        </div>
      </div>
    </header>
  );
}
