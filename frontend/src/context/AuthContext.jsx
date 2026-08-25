import { createContext, useContext, useEffect, useState } from "react";
import * as api from "../lib/api";

const USER_KEY = "viewcast_user";

const AuthContext = createContext(null);

// Plain module-level flag (not React state) so it can be read synchronously
// at redirect-decision time with zero dependency on render/scheduling order.
// See the comment on `logout()` below for why this exists.
let loggingOut = false;

export function isLoggingOut() {
  return loggingOut;
}

function loadStoredUser() {
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(loadStoredUser);
  const [token, setTokenState] = useState(api.getToken);

  function persist(nextToken, nextUser) {
    api.setToken(nextToken);
    if (nextUser) localStorage.setItem(USER_KEY, JSON.stringify(nextUser));
    else localStorage.removeItem(USER_KEY);
    setTokenState(nextToken);
    setUser(nextUser);
  }

  async function login(email, password) {
    const result = await api.login({ email, password });
    persist(result.access_token, result.user);
    return result.user;
  }

  async function signup(fullName, email, password, channelUrl) {
    const result = await api.signup({ fullName, email, password, channelUrl });
    persist(result.access_token, result.user);
    return result.user;
  }

  function logout() {
    // Clearing auth state here re-renders whatever protected page is still
    // mounted (React Router's navigate() to "/" doesn't commit synchronously
    // enough to beat this render), so RequireAuth sees isAuthenticated:false
    // while the URL is still e.g. "/settings" and fires its own redirect
    // with `state: { from: "/settings" }`. That stale "from" would then
    // hijack the *next*, unrelated login and send it back there instead of
    // /dashboard. The `loggingOut` flag tells RequireAuth to skip recording
    // "from" for this specific, deliberate logout.
    loggingOut = true;
    persist(null, null);
    setTimeout(() => {
      loggingOut = false;
    }, 0);
  }

  function setUserData(nextUser) {
    localStorage.setItem(USER_KEY, JSON.stringify(nextUser));
    setUser(nextUser);
  }

  // Keep the stored user fresh (subscriber counts etc.) once per load.
  useEffect(() => {
    if (!token) return;
    api
      .me()
      .then((freshUser) => {
        localStorage.setItem(USER_KEY, JSON.stringify(freshUser));
        setUser(freshUser);
      })
      .catch(() => {
        // Token expired/invalid — drop the stale session.
        persist(null, null);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const value = {
    user,
    token,
    isAuthenticated: Boolean(token),
    login,
    signup,
    logout,
    setUserData,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
