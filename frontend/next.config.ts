import type { NextConfig } from "next";

const apiRewriteTarget =
  process.env.API_REWRITE_TARGET ?? "http://127.0.0.1:8001";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["localhost", "127.0.0.1", "192.168.1.7"],
  experimental: {
    proxyClientMaxBodySize: "50mb",
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiRewriteTarget}/:path*`,
      },
      // Host-based: when the request hostname is helpmateai.xyz (the
      // marketing apex), serve the landing route group from /landing/*.
      // The workspace at app.helpmateai.xyz is unaffected — this rule
      // only fires when the Host header matches the apex domain.
      {
        source: "/:path*",
        has: [{ type: "host", value: "helpmateai.xyz" }],
        destination: "/landing/:path*",
      },
    ];
  },
};

export default nextConfig;
