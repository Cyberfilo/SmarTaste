import type { NextConfig } from "next";

const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["live.menghi.dev", "music.menghi.dev"],
  // Local dev: proxy /api/* to backend (avoids CORS issues)
  // Production (Vercel): NEXT_PUBLIC_API_URL is set, frontend calls backend directly
  async rewrites() {
    if (process.env.NEXT_PUBLIC_API_URL) return [];
    return [
      { source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` },
      { source: "/health", destination: `${BACKEND_URL}/health` },
    ];
  },
};

export default nextConfig;
