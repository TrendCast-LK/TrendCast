import { Link } from "react-router-dom";
import ThemeToggle from "./ThemeToggle";
import NotificationsMenu from "./NotificationsMenu";
import HelpMenu from "./HelpMenu";
import { useAuth } from "../context/AuthContext";

const DEFAULT_AVATAR =
  "https://lh3.googleusercontent.com/aida-public/AB6AXuDuzGuuAwOYAp8ykuKRbuU_EVC-Lh4SGJfhaTFj6vkLIGFJtkhhd9u-WuZHuwGYLWzmpZtIykwNZoMpO1kszmy-tl-We5wV991w8NmGgGSQUnq0fEwTBUXB_9h9wcXjE-M4l7BwDTuVS_o_jJOFQ6m5qx-Uy8A_hxsQtOKFtsvXXt461c6urVcssyEcB10cFRWDKW28lfgQja0MzsBdK5IboOlU4pXpeRhGobIJmcgnzWnsqQGs5KI";

export default function Topbar({ searchPlaceholder = "Search predictions...", avatarUrl }) {
  const { user } = useAuth();

  return (
    <header className="fixed top-0 right-0 w-full z-40 bg-surface/70 backdrop-blur-xl shadow-sm flex justify-between items-center h-16 px-margin-desktop ml-64 pl-[calc(16rem+40px)]">
      <div className="flex-1 flex items-center">
        <div className="relative w-96 hidden md:block">
          <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant">
            search
          </span>
          <input
            className="w-full bg-surface-container-low border border-surface-variant rounded-full py-2 pl-10 pr-4 font-body-md text-body-md focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
            placeholder={searchPlaceholder}
            type="text"
          />
        </div>
      </div>
      <div className="flex items-center gap-4">
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
            src={avatarUrl || user?.channel_thumbnail_url || DEFAULT_AVATAR}
          />
        </Link>
      </div>
    </header>
  );
}
