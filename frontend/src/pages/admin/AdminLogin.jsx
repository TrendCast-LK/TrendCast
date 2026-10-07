import { useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAdminAuth } from "../../context/AdminAuthContext";
import { ApiError } from "../../lib/api";
import ThemeToggle from "../../components/ThemeToggle";

const inputClass =
  "w-full bg-surface-container-lowest border border-outline-variant rounded-lg px-4 py-3 font-body-md text-body-md text-on-surface placeholder-outline input-focus-ring transition-all duration-200";

export default function AdminLogin() {
  const navigate = useNavigate();
  const location = useLocation();
  const { login, isAuthenticated } = useAdminAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  if (isAuthenticated) return <Navigate to="/admin" replace />;

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      navigate(location.state?.from?.pathname || "/admin", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="bg-background min-h-screen flex items-center justify-center relative overflow-hidden font-body-md text-body-md text-on-surface">
      <div className="fixed top-6 right-6 z-20">
        <ThemeToggle />
      </div>

      <main className="w-full max-w-[440px] px-margin-mobile md:px-0 z-10 relative">
        <div className="glass-card rounded-xl p-8 md:p-12 w-full">
          <div className="text-center mb-8 flex flex-col items-center gap-3">
            <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-primary to-secondary flex items-center justify-center text-white shadow-lg">
              <span className="material-symbols-outlined fill">admin_panel_settings</span>
            </div>
            <h1 className="font-headline-lg text-headline-lg text-on-surface tracking-tight">TrendCast Admin</h1>
            <p className="font-body-md text-body-md text-on-surface-variant">
              Staff sign-in. App accounts don&apos;t work here.
            </p>
          </div>

          <form className="space-y-4" onSubmit={handleSubmit}>
            {error && (
              <div className="rounded-lg bg-error-container/60 text-on-error-container px-4 py-3 font-body-md text-body-md text-sm">
                {error}
              </div>
            )}
            <div>
              <label className="block font-label-md text-label-md text-on-surface mb-1" htmlFor="admin-email">
                Email
              </label>
              <input
                className={inputClass}
                id="admin-email"
                autoComplete="username"
                required
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <div>
              <label className="block font-label-md text-label-md text-on-surface mb-1" htmlFor="admin-password">
                Password
              </label>
              <input
                className={inputClass}
                id="admin-password"
                autoComplete="current-password"
                required
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            <button
              className="w-full gradient-primary gradient-primary-glow text-on-primary font-label-md text-label-md rounded-lg py-3 px-4 hover:opacity-90 transition-opacity duration-200 mt-2 disabled:opacity-60 disabled:cursor-not-allowed"
              type="submit"
              disabled={submitting}
            >
              {submitting ? "Signing in…" : "Sign in"}
            </button>
          </form>

          <p className="text-center font-body-md text-body-md text-on-surface-variant mt-6">
            <Link className="text-primary font-medium hover:text-secondary transition-colors duration-200" to="/">
              Back to the creator sign-in
            </Link>
          </p>
        </div>
      </main>
    </div>
  );
}
