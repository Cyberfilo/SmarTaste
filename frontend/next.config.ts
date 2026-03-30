import type { NextConfig } from "next";

const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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
