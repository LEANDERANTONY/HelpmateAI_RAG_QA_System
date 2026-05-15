import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

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

// withSentryConfig wraps the Next config with the Sentry webpack plugin
// (source-map upload at build time + automatic instrumentation of
// route handlers / RSC). When SENTRY_AUTH_TOKEN is unset the plugin
// still runs but skips the upload step — local builds and PR previews
// without Sentry secrets succeed silently. The org / project values
// match the two projects created in Sentry (org: leander-antony-a,
// project: helpmate-frontend).
//
// We deliberately DO NOT enable ``tunnelRoute`` — the proxy adds a
// Next route under /monitoring that bounces Sentry traffic through
// our server to dodge ad blockers, but it would also push every
// Sentry beacon through Vercel's per-request meter and our API
// rewrite path. Direct-to-Sentry is fine for the volume we're at.
const sentryOptions = {
  org: process.env.SENTRY_ORG || "leander-antony-a",
  project: process.env.SENTRY_PROJECT || "helpmate-frontend",
  // Silent in CI; we don't want the build log spammed with Sentry
  // plugin progress unless we're debugging the upload step.
  silent: !process.env.CI,
  // No auto-source-map upload unless an auth token is present. The
  // plugin reads SENTRY_AUTH_TOKEN from the build env; we don't set
  // it ourselves — Vercel injects it via the Sentry integration.
  widenClientFileUpload: true,
  // Hide source maps from the public bundle so the minified JS shipped
  // to users doesn't carry sourceMappingURL comments pointing at
  // unreachable files.
  hideSourceMaps: true,
  // The legacy ``disableLogger`` flag tree-shakes Sentry's own
  // ``logger.log`` calls out of the production bundle. Saves a
  // negligible amount of bytes but keeps the console quieter.
  disableLogger: true,
};

export default withSentryConfig(nextConfig, sentryOptions);
