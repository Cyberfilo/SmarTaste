/**
 * Central API client for backend communication.
 * All requests go to the backend at localhost:8000 (configurable via env var).
 * Handles CSRF tokens, cookie-based auth, and automatic token refresh.
 */

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/**
 * Read the CSRF token from document.cookie.
 */
function getCsrfToken(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : null;
}

/**
 * Fetch wrapper that handles:
 * - Prepending API_URL to path
 * - credentials: "include" for httpOnly cookies
 * - Content-Type: application/json for mutating requests
 * - CSRF token on POST/PUT/DELETE via X-CSRFToken header
 * - 401 -> automatic token refresh -> retry once -> redirect to /login
 */
export async function apiFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const url = `${API_URL}${path}`;
  const method = (options?.method ?? "GET").toUpperCase();
  const isMutating = ["POST", "PUT", "DELETE", "PATCH"].includes(method);

  const headers = new Headers(options?.headers);

  if (isMutating) {
    if (!headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    const csrf = getCsrfToken();
    if (csrf) {
      headers.set("X-CSRFToken", csrf);
    }
  }

  const fetchOptions: RequestInit = {
    ...options,
    headers,
    credentials: "include",
  };

  let response = await fetch(url, fetchOptions);

  // On 401, attempt to refresh the access token and retry once
  if (response.status === 401) {
    const refreshHeaders = new Headers();
    refreshHeaders.set("Content-Type", "application/json");
    const csrf = getCsrfToken();
    if (csrf) {
      refreshHeaders.set("X-CSRFToken", csrf);
    }

    const refreshRes = await fetch(`${API_URL}/api/auth/refresh`, {
      method: "POST",
      headers: refreshHeaders,
      credentials: "include",
    });

    if (refreshRes.ok) {
      // Retry the original request with potentially new CSRF token
      const retryHeaders = new Headers(options?.headers);
      if (isMutating) {
        if (!retryHeaders.has("Content-Type")) {
          retryHeaders.set("Content-Type", "application/json");
        }
        const newCsrf = getCsrfToken();
        if (newCsrf) {
          retryHeaders.set("X-CSRFToken", newCsrf);
        }
      }
      response = await fetch(url, {
        ...options,
        headers: retryHeaders,
        credentials: "include",
      });
    } else {
      // Refresh failed, redirect to login
      if (typeof window !== "undefined") {
        window.location.href = "/login";
      }
      throw new Error("Session expired. Please log in again.");
    }
  }

  if (!response.ok) {
    let errorMessage = `Request failed: ${response.status}`;
    try {
      const errorBody = await response.json();
      errorMessage = errorBody.detail || errorBody.message || errorMessage;
    } catch {
      // response body wasn't JSON
    }
    throw new Error(errorMessage);
  }

  return response.json() as Promise<T>;
}

// ── Auth API ──────────────────────────────────────────────

interface AuthResponse {
  user_id: string;
  email: string;
  message: string;
}

interface MeResponse {
  user_id: string;
  email: string;
  display_name: string;
}

export const authApi = {
  signup: (email: string, password: string, displayName?: string) =>
    apiFetch<AuthResponse>("/api/auth/signup", {
      method: "POST",
      body: JSON.stringify({
        email,
        password,
        ...(displayName ? { display_name: displayName } : {}),
      }),
    }),

  login: (email: string, password: string) =>
    apiFetch<AuthResponse>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  logout: () =>
    apiFetch<{ message: string }>("/api/auth/logout", {
      method: "POST",
    }),

  me: () => apiFetch<MeResponse>("/api/auth/me"),
};
