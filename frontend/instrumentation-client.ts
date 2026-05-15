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

// GDPR / ePrivacy gate. We split the Sentry integrations into two
// categories matching the cookie-banner contract:
//
//   * Always-on (legitimate interest under GDPR Art. 6(1)(f) — we
//     need crash reporting to operate the service securely):
//       - error tracking + traces
//       - User Feedback widget (the report itself is user-initiated;
//         storing it after submission is justified by support)
//   * Consent-gated (requires the user to accept the banner):
//       - Session Replay (records DOM mutations + user input)
//
// At boot we read localStorage["helpmate-cookie-consent"] inline
// (importing the helper would pull React into a config module). If
// it's "accepted" we ship Replay; otherwise we skip it. When the
// user later accepts via the banner, ``addReplayIntegrationAfterConsent``
// (called from the cookie-consent state-change listener) injects
// Replay without a page reload.
function readConsent(): "pending" | "accepted" | "declined" {
  if (typeof window === "undefined") return "pending";
  try {
    const raw = window.localStorage.getItem("helpmate-cookie-consent");
    if (raw === "accepted" || raw === "declined") return raw;
  } catch {
    /* incognito / Safari ITP — treat as pending */
  }
  return "pending";
}

// Derive the integration-array type from Sentry.init's signature so a
// future SDK type change can't silently widen this. ``@sentry/nextjs``
// doesn't re-export the bare ``Integration`` type from its public
// barrel — Parameters<...> is the supported path.
type SentryIntegrations = NonNullable<
  NonNullable<Parameters<typeof Sentry.init>[0]>["integrations"]
>;

function buildIntegrations(consent: "pending" | "accepted" | "declined"): SentryIntegrations {
  const integrations: SentryIntegrations = [
    Sentry.feedbackIntegration({
      colorScheme: "dark",
      // Brand the widget + popup to the product palette: the mint
      // accent (--accent #7fe0b0 with --accent-fg #04241a) on the
      // dark surface (--surface-strong) so the trigger button + form
      // match the workspace instead of Sentry's default purple-on-grey.
      themeDark: {
        background: "#0b0b0b",
        foreground: "#f5f8ff",
        accentBackground: "#7fe0b0",
        accentForeground: "#04241a",
        successColor: "#7fe0b0",
        errorColor: "#ff8b8b",
        boxShadow: "0 16px 48px rgba(0, 0, 0, 0.6)",
        outline: "2px solid rgba(127, 224, 176, 0.6)",
      },
      autoInject: true,
      showBranding: false,
      triggerLabel: "Report an issue",
      formTitle: "Report an issue",
      submitButtonLabel: "Send",
      enableScreenshot: true,
    }),
  ];
  if (consent === "accepted") {
    integrations.push(
      Sentry.replayIntegration({
        // Mask all text + media by default. The workspace shows user
        // documents on screen; we can't ship those to Sentry without
        // making PII commitments we haven't reviewed legally.
        maskAllText: true,
        blockAllMedia: true,
      }),
    );
  }
  return integrations;
}

if (dsn) {
  const consentAtBoot = readConsent();
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
    // error — only when the user has consented. Without consent the
    // replay integration isn't loaded so these numbers are inert.
    replaysSessionSampleRate: 0,
    replaysOnErrorSampleRate:
      consentAtBoot === "accepted"
        ? Number(process.env.NEXT_PUBLIC_SENTRY_REPLAYS_ON_ERROR_SAMPLE_RATE ?? 1.0)
        : 0,
    integrations: buildIntegrations(consentAtBoot),
    debug: false,
  });

  // Hot-add Replay if the user accepts AFTER the initial boot. The
  // cookie-consent component dispatches "helpmate-cookie-consent-change"
  // on every transition; we listen once and re-check the stored value.
  if (typeof window !== "undefined") {
    window.addEventListener("helpmate-cookie-consent-change", () => {
      const next = readConsent();
      if (next === "accepted") {
        try {
          Sentry.addIntegration(
            Sentry.replayIntegration({
              maskAllText: true,
              blockAllMedia: true,
            }),
          );
        } catch {
          /* already added or SDK doesn't support hot-add — fine */
        }
      }
      // We deliberately do NOT tear Replay down on a flip to
      // "declined" mid-session: the SDK doesn't expose a clean
      // removeIntegration path, and the user can hard-reload to fully
      // unsubscribe. Setting opt-out on PostHog (in
      // posthog-provider.tsx) covers the analytics half cleanly.
    });
  }
}

// Required export for Next's instrumentation hook on the client —
// runs once per navigation, lets the Sentry SDK record the route
// transition as a span. Without this, RouterTransitionStart events
// from the App Router don't show up as navigation spans in Sentry.
export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
