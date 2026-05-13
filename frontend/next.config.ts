import type { NextConfig } from "next";

const apiRewriteTarget =
  process.env.API_REWRITE_TARGET ?? "http://127.0.0.1:8001";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["localhost", "127.0.0.1", "192.168.1.7"],
  experimental: {
    proxyClientMaxBodySize: "50mb",
  },
  async rewrites() {
    // beforeFiles runs BEFORE the file-system / page routing check, so
    // host-based rewrites can override the default route match. With the
    // default `afterFiles` bucket, `app/page.tsx` (the workspace) matches
    // `/` first and the rewrite never fires.
    //
    // Explicit per-route rules instead of a catch-all `/:path*` source
    // because a catch-all would also rewrite `/_next/static/*`,
    // `/favicon.ico`, `/apple-icon.png`, etc., breaking Vercel's static
    // serving. The landing only has two URLs — list them.
    return {
      beforeFiles: [
        {
          source: "/",
          has: [{ type: "host", value: "helpmateai.xyz" }],
          destination: "/landing",
        },
        {
          source: "/privacy-policy",
          has: [{ type: "host", value: "helpmateai.xyz" }],
          destination: "/landing/privacy-policy",
        },
      ],
      afterFiles: [
        {
          source: "/api/:path*",
          destination: `${apiRewriteTarget}/:path*`,
        },
      ],
      fallback: [],
    };
  },
};

export default nextConfig;
