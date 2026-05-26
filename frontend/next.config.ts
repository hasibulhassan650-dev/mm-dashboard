import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  env: {
    BUILD_TIME: new Date().toISOString(),
  },
};

export default nextConfig;
