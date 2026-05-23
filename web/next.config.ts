import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  transpilePackages: ["@tremor/react"],
  env: {
    AGENT_BASE_URL: process.env.AGENT_BASE_URL ?? "http://localhost:8000",
    MAPBOX_TOKEN: process.env.MAPBOX_TOKEN ?? "",
  },
};

export default nextConfig;
