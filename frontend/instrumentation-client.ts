// Sentry — Browser runtime config.
//
// Next 15+ replaced the legacy ``sentry.client.config.ts`` convention
// with ``instrumentation-client.ts``, which is loaded once at app
// boot in the browser. We initialize the Sentry browser SDK here and
// turn on the BrowserTracing integration for navigation spans.
//
// The DSN comes from ``NEXT_PUBLIC_SENTRY_DSN`` so the value is inlined
// into the JS bundle at build time. When the DSN is empty, ``init``
// is a no-op — local dev and preview deploys don't have to ship the
// secret.

import * as Sentry from "@sentry/nextjs";

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment:
      process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ||
      process.env.NODE_ENV ||
      "development",
    // Browser traces are cheap on the free tier but they pile up fast
    // for chatty pages. 10% is the Sentry-default + plays nice with the
    // PostHog session replay coverage we layer on top.
    tracesSampleRate: Number(
      process.env.NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE ?? 0.1,
    ),
    // Don't auto-send Replay on the free tier — we already pay for
    // session replay via PostHog and Sentry's quota is small. Flip via
    // env if you decide to consolidate.
    replaysSessionSampleRate: 0,
    replaysOnErrorSampleRate: 0,
    debug: false,
  });
}

// Required export for Next's instrumentation hook on the client —
// runs once per navigation, lets the Sentry SDK record the route
// transition as a span. Without this, RouterTransitionStart events
// from the App Router don't show up as navigation spans in Sentry.
export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
