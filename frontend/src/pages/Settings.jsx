import { useState } from "react";
import Sidebar from "../components/Sidebar";
import Topbar from "../components/Topbar";
import { useAuth } from "../context/AuthContext";
import { useTheme } from "../context/ThemeContext";
import * as api from "../lib/api";
import { ApiError } from "../lib/api";

export default function Settings() {
  const { user, setUserData } = useAuth();

  return (
    <div className="bg-background text-on-background min-h-screen font-body-md selection:bg-primary-fixed selection:text-on-primary-fixed">
      <Sidebar active="settings" />
      <Topbar searchPlaceholder="Search settings..." />

      <main className="ml-64 pt-24 px-margin-desktop pb-24 max-w-[900px] mx-auto flex flex-col gap-10">
        <div>
          <p className="font-label-md text-label-md text-primary mb-2 uppercase tracking-widest">
            Account
          </p>
          <h2 className="font-headline-lg text-headline-lg text-on-background">Settings</h2>
          <p className="font-body-md text-body-md text-on-surface-variant mt-2">
            Manage your profile, channel stats, appearance, and password.
          </p>
        </div>

        <AppearanceSection />
        <ProfileSection user={user} onSaved={setUserData} />
        <PasswordSection />
      </main>
    </div>
  );
}

function AppearanceSection() {
  const { theme, setTheme } = useTheme();

  const options = [
    { key: "light", label: "Light", icon: "light_mode" },
    { key: "dark", label: "Dark", icon: "dark_mode" },
  ];

  return (
    <section className="glass-panel p-8 rounded-xl shadow-[0_10px_30px_rgba(0,0,0,0.04)]">
      <h3 className="font-headline-md text-headline-md text-on-background mb-6 border-b border-outline-variant/50 pb-4">
        Appearance
      </h3>
      <p className="font-body-md text-body-md text-on-surface-variant mb-4">
        Choose how ViewCast looks on this device.
      </p>
      <div className="flex gap-4">
        {options.map((option) => {
          const isActive = theme === option.key;
          return (
            <button
              key={option.key}
              type="button"
              onClick={() => setTheme(option.key)}
              className={
                isActive
                  ? "flex items-center gap-2 px-6 py-3 rounded-xl font-label-md text-label-md bg-gradient-to-r from-primary to-secondary text-on-primary shadow-lg shadow-primary/20"
                  : "flex items-center gap-2 px-6 py-3 rounded-xl font-label-md text-label-md bg-surface-container text-on-surface hover:bg-surface-container-highest transition-colors"
              }
            >
              <span className="material-symbols-outlined text-[20px]">{option.icon}</span>
              {option.label}
            </button>
          );
        })}
      </div>
    </section>
  );
}

function ProfileSection({ user, onSaved }) {
  const [fullName, setFullName] = useState(user?.full_name || "");
  const [subscribers, setSubscribers] = useState(user?.subscribers ?? 0);
  const [monthlyViews, setMonthlyViews] = useState(user?.monthly_views ?? 0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const updated = await api.updateProfile({
        fullName,
        subscribers: Number(subscribers),
        monthlyViews: Number(monthlyViews),
      });
      onSaved(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save changes. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="glass-panel p-8 rounded-xl shadow-[0_10px_30px_rgba(0,0,0,0.04)]">
      <h3 className="font-headline-md text-headline-md text-on-background mb-6 border-b border-outline-variant/50 pb-4">
        Profile
      </h3>

      <form className="space-y-6" onSubmit={handleSubmit}>
        {error && (
          <div className="rounded-lg bg-error-container/60 text-on-error-container px-4 py-3 font-body-md text-body-md text-sm">
            {error}
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div>
            <label className="block font-label-md text-label-md text-on-surface-variant mb-2">Full Name</label>
            <input
              className="input-field font-body-md text-body-md text-on-surface"
              type="text"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
            />
          </div>
          <div>
            <label className="block font-label-md text-label-md text-on-surface-variant mb-2">Email</label>
            <input
              className="input-field font-body-md text-body-md text-on-surface-variant cursor-not-allowed"
              type="email"
              value={user?.email || ""}
              disabled
              title="Email changes aren't supported yet"
            />
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div>
            <label className="block font-label-md text-label-md text-on-surface-variant mb-2">
              Subscribers
            </label>
            <input
              className="input-field font-body-md text-body-md text-on-surface"
              type="number"
              min="0"
              value={subscribers}
              onChange={(e) => setSubscribers(e.target.value)}
            />
            <p className="font-label-sm text-label-sm text-on-surface-variant mt-1">
              Used as the baseline for prediction forecasts.
            </p>
          </div>
          <div>
            <label className="block font-label-md text-label-md text-on-surface-variant mb-2">
              Monthly Views
            </label>
            <input
              className="input-field font-body-md text-body-md text-on-surface"
              type="number"
              min="0"
              value={monthlyViews}
              onChange={(e) => setMonthlyViews(e.target.value)}
            />
          </div>
        </div>

        <div className="flex items-center gap-4">
          <button
            className="px-6 py-3 bg-gradient-to-r from-primary to-secondary text-on-primary rounded-xl font-label-md text-label-md shadow-lg shadow-primary/20 hover:scale-[1.02] transition-transform active:scale-95 disabled:opacity-60 disabled:cursor-not-allowed disabled:hover:scale-100"
            type="submit"
            disabled={saving}
          >
            {saving ? "Saving…" : "Save Changes"}
          </button>
          {saved && (
            <span className="font-label-md text-label-md text-primary flex items-center gap-1">
              <span className="material-symbols-outlined text-sm">check_circle</span> Saved
            </span>
          )}
        </div>
      </form>
    </section>
  );
}

function PasswordSection() {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setSaved(false);

    if (newPassword !== confirmPassword) {
      setError("New passwords don't match.");
      return;
    }

    setSaving(true);
    try {
      await api.changePassword({ currentPassword, newPassword });
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't change password. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="glass-panel p-8 rounded-xl shadow-[0_10px_30px_rgba(0,0,0,0.04)]">
      <h3 className="font-headline-md text-headline-md text-on-background mb-6 border-b border-outline-variant/50 pb-4">
        Password
      </h3>

      <form className="space-y-6" onSubmit={handleSubmit}>
        {error && (
          <div className="rounded-lg bg-error-container/60 text-on-error-container px-4 py-3 font-body-md text-body-md text-sm">
            {error}
          </div>
        )}

        <div>
          <label className="block font-label-md text-label-md text-on-surface-variant mb-2">
            Current Password
          </label>
          <input
            className="input-field font-body-md text-body-md text-on-surface"
            type="password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            required
          />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div>
            <label className="block font-label-md text-label-md text-on-surface-variant mb-2">
              New Password
            </label>
            <input
              className="input-field font-body-md text-body-md text-on-surface"
              type="password"
              minLength={8}
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
            />
          </div>
          <div>
            <label className="block font-label-md text-label-md text-on-surface-variant mb-2">
              Confirm New Password
            </label>
            <input
              className="input-field font-body-md text-body-md text-on-surface"
              type="password"
              minLength={8}
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
            />
          </div>
        </div>

        <div className="flex items-center gap-4">
          <button
            className="px-6 py-3 bg-surface-container text-on-surface rounded-xl font-label-md text-label-md hover:bg-surface-container-highest transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
            type="submit"
            disabled={saving}
          >
            {saving ? "Updating…" : "Update Password"}
          </button>
          {saved && (
            <span className="font-label-md text-label-md text-primary flex items-center gap-1">
              <span className="material-symbols-outlined text-sm">check_circle</span> Password updated
            </span>
          )}
        </div>
      </form>
    </section>
  );
}
