import { useState } from "react";
import { Link, useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { ApiError } from "../lib/api";
import ThemeToggle from "../components/ThemeToggle";

export default function SignIn() {
  const navigate = useNavigate();
  const location = useLocation();
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      const redirectTo = location.state?.from?.pathname || "/dashboard";
      navigate(redirectTo, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="bg-background min-h-screen flex items-center justify-center relative overflow-hidden font-body-md text-body-md text-on-surface">
      <div className="absolute top-0 left-0 w-full h-full overflow-hidden z-0 pointer-events-none">
        <div className="absolute top-[-20%] left-[-10%] w-[60vw] h-[60vw] rounded-full bg-primary-fixed opacity-30 blur-[100px]" />
        <div className="absolute bottom-[-20%] right-[-10%] w-[50vw] h-[50vw] rounded-full bg-secondary-fixed opacity-30 blur-[100px]" />
      </div>

      <div className="fixed top-6 right-6 z-20">
        <ThemeToggle />
      </div>

      <main className="w-full max-w-[480px] px-margin-mobile md:px-0 z-10 relative">
        <div className="glass-card rounded-xl p-8 md:p-12 w-full transition-all duration-300 hover:shadow-[0px_20px_40px_rgba(0,0,0,0.08)]">
          <div className="text-center mb-8">
            <h1 className="font-headline-lg text-headline-lg text-on-surface mb-2 tracking-tight">
              TrendCast
            </h1>
          </div>

          <div className="space-y-6">
            <div className="relative flex items-center py-2">
              <div className="flex-grow border-t border-outline-variant" />
              <span className="flex-shrink-0 mx-4 font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider">
                Sign in with email
              </span>
              <div className="flex-grow border-t border-outline-variant" />
            </div>

            <form className="space-y-4" onSubmit={handleSubmit}>
              {error && (
                <div className="rounded-lg bg-error-container/60 text-on-error-container px-4 py-3 font-body-md text-body-md text-sm">
                  {error}
                </div>
              )}
              <div>
                <label className="block font-label-md text-label-md text-on-surface mb-1" htmlFor="email">
                  Email
                </label>
                <input
                  className="w-full bg-surface-container-lowest border border-outline-variant rounded-lg px-4 py-3 font-body-md text-body-md text-on-surface placeholder-outline input-focus-ring transition-all duration-200"
                  id="email"
                  name="email"
                  placeholder="name@company.com"
                  required
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
              <div>
                <div className="flex justify-between items-center mb-1">
                  <label className="block font-label-md text-label-md text-on-surface" htmlFor="password">
                    Password
                  </label>
                  <a className="font-label-md text-label-md text-primary hover:text-secondary transition-colors duration-200" href="#forgot">
                    Forgot password?
                  </a>
                </div>
                <input
                  className="w-full bg-surface-container-lowest border border-outline-variant rounded-lg px-4 py-3 font-body-md text-body-md text-on-surface placeholder-outline input-focus-ring transition-all duration-200"
                  id="password"
                  name="password"
                  placeholder="••••••••"
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
                {submitting ? "Signing in…" : "Sign In"}
              </button>
            </form>

            <p className="text-center font-body-md text-body-md text-on-surface-variant mt-6">
              Don&apos;t have an account?{" "}
              <Link className="text-primary font-medium hover:text-secondary transition-colors duration-200" to="/sign-up">
                Sign up
              </Link>
            </p>

            <p className="text-center font-label-sm text-label-sm text-on-surface-variant">
              <Link
                className="inline-flex items-center gap-1 hover:text-primary transition-colors duration-200"
                to="/admin/login"
              >
                <span className="material-symbols-outlined text-[16px]">admin_panel_settings</span>
                Admin sign-in
              </Link>
            </p>
          </div>
        </div>
      </main>
    </div>
  );
}
