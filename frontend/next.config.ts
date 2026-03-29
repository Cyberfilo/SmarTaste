import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["live.menghi.dev", "music.menghi.dev"],
  // Local dev: proxy API calls to local backend.
  // Production (Vercel): rewrites are handled by vercel.json instead.
  async rewrites() {
    if (process.env.VERCEL) return [];
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/api/:path*",
      },
      {
        source: "/health",
        destination: "http://localhost:8000/health",
      },
    ];
  },
};

export default nextConfig;
