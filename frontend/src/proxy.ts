import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * Next.js proxy for route protection (renamed from middleware in Next.js 16).
 *
 * - If accessing protected routes without access_token cookie -> redirect to /login
 * - If accessing /login or /signup with access_token cookie -> redirect to /dashboard
 *
 * Note: This runs on Edge and only checks cookie existence.
 * Full JWT validation happens server-side via GET /api/auth/me in the (app) layout.
 */
export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const hasToken = request.cookies.has("access_token");

  // Protected routes: redirect unauthenticated users to /login
  const protectedPaths = ["/dashboard", "/chat", "/settings"];
  const isProtected = protectedPaths.some(
    (p) => pathname === p || pathname.startsWith(p + "/"),
  );

  if (isProtected && !hasToken) {
    const loginUrl = new URL("/login", request.url);
    return NextResponse.redirect(loginUrl);
  }

  // Auth routes: redirect authenticated users to /dashboard
  const authPaths = ["/login", "/signup"];
  const isAuthPage = authPaths.includes(pathname);

  if (isAuthPage && hasToken) {
    const dashboardUrl = new URL("/dashboard", request.url);
    return NextResponse.redirect(dashboardUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/dashboard/:path*", "/chat/:path*", "/settings/:path*", "/login", "/signup"],
};
