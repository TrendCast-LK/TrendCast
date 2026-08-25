import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
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

export default function SignUp() {
  const navigate = useNavigate();
  const { signup } = useAuth();
  const [showPassword, setShowPassword] = useState(false);
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [channelUrl, setChannelUrl] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await signup(fullName, email, password, channelUrl);
      navigate("/dashboard", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="bg-background text-on-surface antialiased min-h-screen flex selection:bg-primary-container selection:text-on-primary-container">
      <div className="fixed top-6 right-6 z-20">
        <ThemeToggle />
      </div>
      <div className="flex w-full min-h-screen">
        {/* Left value-prop panel */}
        <div className="hidden lg:flex w-1/2 flex-col justify-between p-12 gradient-bg relative overflow-hidden">
          <div
            className="absolute inset-0 z-0 opacity-30 mix-blend-overlay"
            style={{
              backgroundImage:
                "url('https://lh3.googleusercontent.com/aida-public/AB6AXuC93pR7xSH84OBkKuaMej0NPraC5dXbGSPMXo4WCjlt-QouBMFhePPDzADvKN4BYFACnFugq6RBuNg0c_qVotor7P2qS2F2qqaHP2B-Zr03QalCbqRs9d4Hm-_ABWSSaXnWgCz3f3PN9at_5ty0Iw2w8cZRdlVUCAH8crve-98pZ51EmjhEJVzGSkQdWdcTOCMS8qX75vKG6VSXZFD7dTtLLTcJAIvWfR_uRDe-n-h80m0E94vgPAs')",
              backgroundSize: "cover",
              backgroundPosition: "center",
            }}
          />
          <div className="relative z-10 flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center text-on-primary">
              <span className="material-symbols-outlined text-[20px]">auto_awesome</span>
            </div>
            <span className="font-headline-md text-headline-md font-bold text-on-surface tracking-tight">
              ViewCast
            </span>
          </div>
          <div className="relative z-10 max-w-lg">
            <h1 className="font-display-lg text-display-lg font-bold text-on-surface mb-6 leading-tight">
              Predict Your
              <br />
              <span className="text-transparent bg-clip-text bg-gradient-to-r from-primary to-secondary">
                Success.
              </span>
            </h1>
            <p className="font-body-lg text-body-lg text-on-surface-variant mb-12">
              Join thousands of creators using predictive analytics to optimize their content
              strategy, forecast growth, and maximize engagement before hitting publish.
            </p>
            <div className="space-y-6">
              <div className="flex items-start gap-4">
                <div className="w-10 h-10 rounded-full bg-white/50 backdrop-blur-sm flex items-center justify-center flex-shrink-0 text-primary">
                  <span className="material-symbols-outlined">analytics</span>
                </div>
                <div>
                  <h3 className="font-label-md text-label-md font-semibold text-on-surface mb-1">
                    Data-Driven Decisions
                  </h3>
                  <p className="font-body-md text-body-md text-on-surface-variant text-sm">
                    Stop guessing. Let AI analyze your past performance to suggest winning topics.
                  </p>
                </div>
              </div>
              <div className="flex items-start gap-4">
                <div className="w-10 h-10 rounded-full bg-white/50 backdrop-blur-sm flex items-center justify-center flex-shrink-0 text-secondary">
                  <span className="material-symbols-outlined">trending_up</span>
                </div>
                <div>
                  <h3 className="font-label-md text-label-md font-semibold text-on-surface mb-1">
                    Growth Forecasting
                  </h3>
                  <p className="font-body-md text-body-md text-on-surface-variant text-sm">
                    See predicted trajectory for subscribers and views up to 90 days in advance.
                  </p>
                </div>
              </div>
            </div>
          </div>
          <div className="relative z-10 font-body-md text-body-md text-sm text-on-surface-variant">
            © {new Date().getFullYear()} ViewCast. All rights reserved.
          </div>
        </div>

        {/* Sign-up form */}
        <div className="w-full lg:w-1/2 flex items-center justify-center p-6 sm:p-12 md:p-margin-desktop bg-surface-container-lowest">
          <div className="w-full max-w-md">
            <div className="flex items-center gap-3 lg:hidden mb-12 justify-center">
              <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center text-on-primary">
                <span className="material-symbols-outlined text-[20px]">auto_awesome</span>
              </div>
              <span className="font-headline-md text-headline-md font-bold text-on-surface tracking-tight">
                ViewCast
              </span>
            </div>

            <div className="glass-card rounded-2xl p-8 sm:p-10 w-full">
              <div className="mb-8">
                <h2 className="font-headline-md text-headline-md font-bold text-on-surface mb-2">
                  Create an account
                </h2>
                <p className="font-body-md text-body-md text-on-surface-variant">
                  Enter your details below to get started.
                </p>
              </div>

              <form className="space-y-5" onSubmit={handleSubmit}>
                {error && (
                  <div className="rounded-lg bg-error-container/60 text-on-error-container px-4 py-3 font-body-md text-body-md text-sm">
                    {error}
                  </div>
                )}
                <div className="space-y-1.5">
                  <label className="font-label-md text-label-md text-on-surface block" htmlFor="fullName">
                    Full Name
                  </label>
                  <div className="relative input-focus-ring rounded-lg border border-outline-variant bg-surface-container-lowest transition-all duration-200">
                    <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-on-surface-variant">
                      <span className="material-symbols-outlined text-[20px]">person</span>
                    </div>
                    <input
                      className="block w-full pl-10 pr-3 py-2.5 bg-transparent border-none focus:ring-0 font-body-md text-body-md text-on-surface placeholder:text-outline"
                      id="fullName"
                      placeholder="Jane Doe"
                      required
                      type="text"
                      value={fullName}
                      onChange={(e) => setFullName(e.target.value)}
                    />
                  </div>
                </div>

                <div className="space-y-1.5">
                  <label className="font-label-md text-label-md text-on-surface block" htmlFor="email">
                    Email
                  </label>
                  <div className="relative input-focus-ring rounded-lg border border-outline-variant bg-surface-container-lowest transition-all duration-200">
                    <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-on-surface-variant">
                      <span className="material-symbols-outlined text-[20px]">mail</span>
                    </div>
                    <input
                      className="block w-full pl-10 pr-3 py-2.5 bg-transparent border-none focus:ring-0 font-body-md text-body-md text-on-surface placeholder:text-outline"
                      id="email"
                      placeholder="jane@example.com"
                      required
                      type="email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                    />
                  </div>
                </div>

                <div className="space-y-1.5">
                  <label className="font-label-md text-label-md text-on-surface block" htmlFor="channelUrl">
                    YouTube Channel URL
                  </label>
                  <div className="relative input-focus-ring rounded-lg border border-outline-variant bg-surface-container-lowest transition-all duration-200">
                    <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-on-surface-variant">
                      <span className="material-symbols-outlined text-[20px]">smart_display</span>
                    </div>
                    <input
                      className="block w-full pl-10 pr-3 py-2.5 bg-transparent border-none focus:ring-0 font-body-md text-body-md text-on-surface placeholder:text-outline"
                      id="channelUrl"
                      placeholder="https://www.youtube.com/@yourchannel"
                      required
                      type="url"
                      value={channelUrl}
                      onChange={(e) => setChannelUrl(e.target.value)}
                    />
                  </div>
                  <p className="font-body-md text-body-md text-xs text-on-surface-variant mt-1">
                    We'll pull your channel's public stats to personalize your forecasts.
                  </p>
                </div>

                <div className="space-y-1.5">
                  <label className="font-label-md text-label-md text-on-surface block" htmlFor="password">
                    Password
                  </label>
                  <div className="relative input-focus-ring rounded-lg border border-outline-variant bg-surface-container-lowest transition-all duration-200">
                    <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-on-surface-variant">
                      <span className="material-symbols-outlined text-[20px]">lock</span>
                    </div>
                    <input
                      className="block w-full pl-10 pr-10 py-2.5 bg-transparent border-none focus:ring-0 font-body-md text-body-md text-on-surface placeholder:text-outline"
                      id="password"
                      placeholder="••••••••"
                      required
                      minLength={8}
                      type={showPassword ? "text" : "password"}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                    />
                    <button
                      className="absolute inset-y-0 right-0 pr-3 flex items-center text-on-surface-variant hover:text-primary transition-colors"
                      type="button"
                      onClick={() => setShowPassword((v) => !v)}
                      aria-label={showPassword ? "Hide password" : "Show password"}
                    >
                      <span className="material-symbols-outlined text-[20px]">
                        {showPassword ? "visibility" : "visibility_off"}
                      </span>
                    </button>
                  </div>
                  <p className="font-body-md text-body-md text-xs text-on-surface-variant mt-1">
                    Must be at least 8 characters long.
                  </p>
                </div>

                <button
                  className="w-full btn-gradient text-on-primary font-label-md text-label-md py-3 rounded-lg flex justify-center items-center gap-2 mt-6 disabled:opacity-60 disabled:cursor-not-allowed"
                  type="submit"
                  disabled={submitting}
                >
                  {submitting ? "Creating account…" : "Create Account"}
                  <span className="material-symbols-outlined text-[18px]">arrow_forward</span>
                </button>
              </form>

              <div className="mt-8 mb-8 relative">
                <div className="absolute inset-0 flex items-center">
                  <div className="w-full border-t border-outline-variant" />
                </div>
                <div className="relative flex justify-center text-sm">
                  <span className="px-4 bg-white text-on-surface-variant font-body-md text-body-md text-sm">
                    Or continue with
                  </span>
                </div>
              </div>

              <button
                className="w-full bg-surface-container-lowest border border-outline-variant hover:bg-surface-container-low transition-colors text-on-surface font-label-md text-label-md py-2.5 rounded-lg flex justify-center items-center gap-3"
                type="button"
              >
                <GoogleIcon />
                Google
              </button>

              <p className="mt-8 text-center font-body-md text-body-md text-sm text-on-surface-variant">
                Already have an account?{" "}
                <Link
                  className="font-label-md text-label-md font-semibold text-primary hover:text-primary-container transition-colors"
                  to="/"
                >
                  Sign in
                </Link>
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
