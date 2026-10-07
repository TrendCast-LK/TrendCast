// Admin dashboard API. Admins are separate accounts with their own token,
// stored under a different key so an admin session and an app session never
// overwrite each other in the same browser.
import { request } from "./api";

const ADMIN_TOKEN_KEY = "trendcast_admin_token";

export function getAdminToken() {
  return localStorage.getItem(ADMIN_TOKEN_KEY);
}

export function setAdminToken(token) {
  if (token) localStorage.setItem(ADMIN_TOKEN_KEY, token);
  else localStorage.removeItem(ADMIN_TOKEN_KEY);
}

function adminRequest(path, options = {}) {
  return request(path, { ...options, token: getAdminToken() || "" });
}

function query(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, value);
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

// ---- Auth ---------------------------------------------------------------

export function adminLogin({ email, password }) {
  const form = new URLSearchParams();
  form.set("username", email);
  form.set("password", password);
  return request("/admin/auth/login", { method: "POST", auth: false, form });
}

export function adminMe() {
  return adminRequest("/admin/auth/me");
}

// ---- Overview -------------------------------------------------------------

export function getOverview({ ensembleSince } = {}) {
  return adminRequest(`/admin/overview${query({ ensemble_since: ensembleSince })}`);
}

// ---- Users ------------------------------------------------------------------

export function listUsers({ q, status, limit, offset } = {}) {
  return adminRequest(`/admin/users${query({ q, status, limit, offset })}`);
}

export function getUser(id) {
  return adminRequest(`/admin/users/${id}`);
}

export function disableUser(id) {
  return adminRequest(`/admin/users/${id}/disable`, { method: "POST" });
}

export function enableUser(id) {
  return adminRequest(`/admin/users/${id}/enable`, { method: "POST" });
}

export function refreshUserChannel(id) {
  return adminRequest(`/admin/users/${id}/refresh-channel`, { method: "POST" });
}

export function clearUserFetchError(id) {
  return adminRequest(`/admin/users/${id}/clear-fetch-error`, { method: "POST" });
}

export function deleteUser(id, confirmEmail) {
  return adminRequest(`/admin/users/${id}`, { method: "DELETE", json: { confirm_email: confirmEmail } });
}

// ---- Audit log ----------------------------------------------------------------

export function listAuditLog({ action, limit, offset } = {}) {
  return adminRequest(`/admin/audit-log${query({ action, limit, offset })}`);
}
