// CreditMind API client. The token lives in sessionStorage: it is cleared when the tab closes.
export const API_BASE = (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/$/, "");
const TOKEN_KEY = "creditmind_token";

export const getToken = () => sessionStorage.getItem(TOKEN_KEY);
export const setToken = (token) =>
  token ? sessionStorage.setItem(TOKEN_KEY, token) : sessionStorage.removeItem(TOKEN_KEY);

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : "Request failed");
    this.status = status;
    this.detail = detail;
  }
}

export async function api(path, { method = "GET", body } = {}) {
  const headers = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, `Cannot reach the API at ${API_BASE}. Check that it is running.`);
  }

  if (response.status === 401 && path !== "/auth/login") {
    setToken(null);
    window.dispatchEvent(new Event("auth:expired"));
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, data?.detail ?? response.statusText);
  return data;
}

// Turn an API error into readable lines (validation errors arrive as a list)
export function errorLines(error) {
  if (error?.detail?.errors) return error.detail.errors;
  if (Array.isArray(error?.detail)) return error.detail.map((d) => d.msg || JSON.stringify(d));
  return [error?.message || "Something went wrong"];
}