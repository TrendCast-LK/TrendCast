import { useState } from "react";
import { Link, useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { ApiError } from "../lib/api";
import ThemeToggle from "../components/ThemeToggle";

function GoogleIcon() {
  return (
    <svg className="w-5 h-5" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
      <path
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
        fill="#4285F4"
      />
      <path
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
        fill="#34A853"
      />
      <path
        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
        fill="#FBBC05"
      />
      <path
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
        fill="#EA4335"
      />
    </svg>
  );
}

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
              ViewCast
            </h1>
            <p className="font-body-md text-body-md text-on-surface-variant">
              Predictive Brilliance for Creators
            </p>
          </div>

          <div className="space-y-6">
            <button
              className="w-full flex items-center justify-center gap-3 bg-surface-container-lowest border border-outline-variant rounded-lg py-3 px-4 font-label-md text-label-md text-on-surface hover:bg-surface-container-low transition-colors duration-200"
              type="button"
            >
              <GoogleIcon />
              Continue with Google
            </button>

            <div className="relative flex items-center py-2">
              <div className="flex-grow border-t border-outline-variant" />
              <span className="flex-shrink-0 mx-4 font-label-sm text-label-sm text-on-surface-variant uppercase tracking-wider">
                or sign in with email
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
          </div>
        </div>
      </main>
    </div>
  );
}
