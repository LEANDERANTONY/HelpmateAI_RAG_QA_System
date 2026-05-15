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
    // Replay strategy: skip ambient session sampling (PostHog handles
    // full session replay), but capture 100% of sessions that hit an
    // error. The on-error path is the high-signal one — the user just
    // saw a workspace blow up and we want to see exactly what they
    // clicked. The free tier covers 50 sessions/month which is plenty
    // for an MVP.
    replaysSessionSampleRate: 0,
    replaysOnErrorSampleRate: Number(
      process.env.NEXT_PUBLIC_SENTRY_REPLAYS_ON_ERROR_SAMPLE_RATE ?? 1.0,
    ),
    integrations: [
      Sentry.replayIntegration({
        // Mask all text + media by default. The workspace shows user
        // documents on screen; we can't ship those to Sentry without
        // making PII commitments we haven't reviewed legally.
        maskAllText: true,
        blockAllMedia: true,
      }),
      // User Feedback widget — Sentry injects a floating "Report a
      // bug" button into the DOM. Tying user-submitted reports to
      // the current Sentry session gives us the breadcrumb trail +
      // active replay along with whatever the user typed. Free on
      // the Developer plan. Themed dark to match the workspace shell;
      // the screenshot capture is opt-in so we don't accidentally
      // ship document content.
      Sentry.feedbackIntegration({
        colorScheme: "dark",
        autoInject: true,
        showBranding: false,
        triggerLabel: "Report an issue",
        formTitle: "Report an issue",
        submitButtonLabel: "Send",
        // Screenshot capture is allowed but defaults to off — the
        // user has to tick the box. Keeps PDF rendering safe by
        // default while letting users opt-in when the bug is visual.
        enableScreenshot: true,
      }),
    ],
    debug: false,
  });
}

// Required export for Next's instrumentation hook on the client —
// runs once per navigation, lets the Sentry SDK record the route
// transition as a span. Without this, RouterTransitionStart events
// from the App Router don't show up as navigation spans in Sentry.
export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
