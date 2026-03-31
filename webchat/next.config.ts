import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  async rewrites() {
    const gateway = process.env.NEXT_PUBLIC_GATEWAY_URL || "http://localhost:8765";
    return [
      {
        source: "/api/:path*",
        destination: `${gateway}/api/:path*`,
      },
      {
        source: "/webchat/:path*",
        destination: `${gateway}/webchat/:path*`,
      },
      {
        source: "/__health",
        destination: `${gateway}/__health`,
      },
    ];
  },
};

export default nextConfig;
