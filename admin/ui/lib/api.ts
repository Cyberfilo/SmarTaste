/**
 * Minimal API client for the admin dashboard.
 *
 * Every request goes to the same-origin Python proxy (admin/app.py), which:
 * - Validates the admin_token HMAC cookie
 * - Forwards /api/* to the backend with the X-Admin-Secret header injected
 *
 * On 401 the Python app responds instead of redirecting, so we push the
 * browser to /login ourselves.
 */
export async function apiFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const res = await fetch(path, {
    ...options,
    credentials: "same-origin",
    headers: {
      ...(options?.body ? { "Content-Type": "application/json" } : {}),
      ...(options?.headers ?? {}),
    },
  });

  if (res.status === 401) {
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
    throw new Error("Session expired");
  }

  if (!res.ok) {
    let msg = `Request failed: ${res.status}`;
    try {
      const body = await res.json();
      msg = body.detail || body.message || msg;
    } catch {
      // not JSON
    }
    throw new Error(msg);
  }

  return res.json() as Promise<T>;
}
