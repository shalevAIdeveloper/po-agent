import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Self-contained server (`.next/standalone/server.js`) for the Docker image.
  output: "standalone",
  // No `X-Powered-By: Next.js` header on deployed responses.
  poweredByHeader: false,
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
