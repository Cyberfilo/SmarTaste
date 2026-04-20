import type { NextConfig } from "next";

// Production backend is hardcoded so Vercel builds proxy correctly even
// if NEXT_PUBLIC_API_URL doesn't propagate. Env var still wins for local
// dev (defaults to localhost:8000) and staging preview deploys.
const BACKEND_URL =
  process.env.NEXT_PUBLIC_API_URL ||
  (process.env.VERCEL_ENV === "production"
    ? "https://musicmind-production.up.railway.app"
    : "http://localhost:8000");

const nextConfig: NextConfig = {
  allowedDevOrigins: ["live.menghi.dev", "music.menghi.dev"],
  // Always proxy /api/* to backend — keeps requests same-origin so
  // cookies work without cross-domain CORS complications.
  // Local: proxies to localhost:8000
  // Vercel: proxies to Railway backend via NEXT_PUBLIC_API_URL
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` },
      { source: "/health", destination: `${BACKEND_URL}/health` },
    ];
  },
};

export default nextConfig;
