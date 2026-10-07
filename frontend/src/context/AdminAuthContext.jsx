import { createContext, useContext, useEffect, useState } from "react";
import * as adminApi from "../lib/adminApi";

// Separate from AuthContext: an admin session is its own account and token,
// and signing in or out of one never touches the other.
const ADMIN_KEY = "trendcast_admin";

const AdminAuthContext = createContext(null);

function loadStoredAdmin() {
  try {
    const raw = localStorage.getItem(ADMIN_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function AdminAuthProvider({ children }) {
  const [admin, setAdmin] = useState(loadStoredAdmin);
  const [token, setTokenState] = useState(adminApi.getAdminToken);

  function persist(nextToken, nextAdmin) {
    adminApi.setAdminToken(nextToken);
    if (nextAdmin) localStorage.setItem(ADMIN_KEY, JSON.stringify(nextAdmin));
    else localStorage.removeItem(ADMIN_KEY);
    setTokenState(nextToken);
    setAdmin(nextAdmin);
  }

  async function login(email, password) {
    const result = await adminApi.adminLogin({ email, password });
    persist(result.access_token, result.admin);
    return result.admin;
  }

  function logout() {
    persist(null, null);
  }

  // For page-level catch blocks: an expired or revoked admin token signs out
  // (RequireAdmin then redirects to the login); anything else is a message.
  function errorMessage(err, fallback = "Something went wrong.") {
    if (err?.status === 401) persist(null, null);
    return err?.message || fallback;
  }

  // Admin tokens are short-lived (8h): drop a stale session on load.
  useEffect(() => {
    if (!token) return;
    adminApi
      .adminMe()
      .then((fresh) => {
        localStorage.setItem(ADMIN_KEY, JSON.stringify(fresh));
        setAdmin(fresh);
      })
      .catch(() => persist(null, null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const value = { admin, token, isAuthenticated: Boolean(token), login, logout, errorMessage };
  return <AdminAuthContext.Provider value={value}>{children}</AdminAuthContext.Provider>;
}

export function useAdminAuth() {
  const ctx = useContext(AdminAuthContext);
  if (!ctx) throw new Error("useAdminAuth must be used within an AdminAuthProvider");
  return ctx;
}
