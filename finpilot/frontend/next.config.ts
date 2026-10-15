import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // a self-contained server.js + traced node_modules, for the Docker image
  output: "standalone",
};

export default nextConfig;
