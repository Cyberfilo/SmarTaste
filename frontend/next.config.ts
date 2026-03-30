import type { NextConfig } from "next";

const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["live.menghi.dev", "music.menghi.dev"],
};

export default nextConfig;
