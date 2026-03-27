/**
 * Auth utilities for the frontend.
 */

import { useAuthStore } from "@/stores/auth-store";

/**
 * Check authentication status. Returns the user if authenticated,
 * otherwise redirects to /login.
 */
export async function requireAuth() {
  const store = useAuthStore.getState();
  const user = await store.checkAuth();
  if (!user && typeof window !== "undefined") {
    window.location.href = "/login";
  }
  return user;
}

/**
 * Read CSRF token from cookies.
 */
export function getCsrfToken(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : null;
}
