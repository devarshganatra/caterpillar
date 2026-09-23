export const API_BASE = "http://localhost:8000";

/**
 * Custom fetch wrapper that automatically attaches the JWT and handles 401s.
 */
export async function apiFetch(endpoint: string, options: RequestInit = {}) {
  const token = localStorage.getItem("jwt_token");
  
  const headers = new Headers(options.headers || {});
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  
  if (!headers.has("Content-Type") && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers,
  });

  if (response.status === 401) {
    // Clear token and trigger a global event so AuthContext can log out
    localStorage.removeItem("jwt_token");
    window.dispatchEvent(new Event("auth:unauthorized"));
  }

  return response;
}
