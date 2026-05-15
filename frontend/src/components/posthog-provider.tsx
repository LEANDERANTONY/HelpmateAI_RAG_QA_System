"use client";

/**
 * PostHogProvider — initializes posthog-js on first client render and
 * keeps it tied to the Supabase user across navigation.
 *
 * Wired in ``src/app/layout.tsx`` so every route (workspace, landing,
 * auth) emits page-view + autocapture events into the same project.
 *
 * Identity flow:
 *   1. On mount, ``posthog.init`` runs once. We use ``persistence:
 *      "localStorage+cookie"`` so the distinct_id survives a hard
 *      reload but a logged-out user is reset to anonymous on
 *      explicit ``posthog.reset()``.
 *   2. The workspace shell calls ``identifyPostHogUser`` once the
 *      Supabase user resolves; that's what pairs the anonymous
 *      pre-login session to the authenticated id (preserving the
 *      funnel — anonymous lands → signs up → asks first question).
 *
 * Failure modes:
 *   • NEXT_PUBLIC_POSTHOG_KEY unset → the provider renders children
 *     unchanged and emits no events. Useful for local dev.
 *   • posthog-js fails to load (network blocked, ad blocker) → the
 *     ``try/catch`` around init keeps a broken analytics import from
 *     blocking the workspace render.
 *
 * Pageview tracking lives in ``PostHogPageView`` (mounted next to
 * this provider). It's a separate component because Suspense — Next
 * 15+ requires ``useSearchParams`` consumers to be wrapped in their
 * own ``<Suspense>`` boundary, and we don't want that boundary to
 * also block PostHog init for children below it.
 */

import { Suspense, useEffect } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import posthog from "posthog-js";

import { useCookieConsent } from "@/components/cookie-consent";

type PostHogProviderProps = {
  children: React.ReactNode;
};

function initPostHog(): void {
  const key = process.env.NEXT_PUBLIC_POSTHOG_KEY;
  if (!key) return;
  if (typeof window === "undefined") return;
  // Re-init guard. React StrictMode mounts the effect twice in dev,
  // and a SPA navigation through layout.tsx would re-run useEffect on
  // every route transition without this. The SDK's __loaded flag is
  // the canonical "is this initialized?" check.
  if ((posthog as unknown as { __loaded?: boolean }).__loaded) return;
  try {
    posthog.init(key, {
      api_host: process.env.NEXT_PUBLIC_POSTHOG_HOST || "https://eu.i.posthog.com",
      // Manual page-view capture via PostHogPageView below. The
      // built-in capture_pageview path listens for ``popstate`` /
      // ``hashchange`` which the App Router does NOT fire on
      // navigation — that path would emit zero pageviews on a SPA
      // navigation, leaving the Web Analytics dashboard empty.
      capture_pageview: false,
      // Capture form submits + clicks. Plenty for funnel building
      // without the noise of every input change.
      autocapture: true,
      // Session replay is included in the PostHog free tier (5K
      // replays/month). Useful for diagnosing failed /qa flows.
      session_recording: {
        maskAllInputs: true,
      },
      // Honor Do-Not-Track. Cheap signal of intent + protects against
      // a privacy-conscious user accidentally getting tracked.
      respect_dnt: true,
      persistence: "localStorage+cookie",
    });
  } catch (err) {
    // Swallow — analytics must never break the page. The dev console
    // surfaces the error for visibility.
    // eslint-disable-next-line no-console
    console.warn("[posthog] init failed", err);
  }
}

/**
 * Manually capture ``$pageview`` events on every App Router
 * navigation. Wraps the ``useSearchParams`` hook in Suspense per the
 * Next 15+ requirement; that's why this lives in its own component.
 */
function PostHogPageView(): null {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!(posthog as unknown as { __loaded?: boolean }).__loaded) return;
    const url = pathname + (searchParams?.toString() ? `?${searchParams.toString()}` : "");
    posthog.capture("$pageview", { $current_url: window.location.origin + url });
  }, [pathname, searchParams]);
  return null;
}

/**
 * Tie the current PostHog session to a Supabase user id. Safe to call
 * with the same id on every render — posthog-js dedupes identify
 * calls internally. Passing ``null`` resets the session to anonymous
 * (logout flow).
 *
 * The ``traits`` argument lets you attach a few user properties for
 * cohort building — we use ``tier`` and ``email`` from the app
 * workspace. Tier is also set via ``posthog.group('tier', tier)`` so
 * we can run group-level analytics (free vs pro funnels) in addition
 * to per-user properties.
 */
export function identifyPostHogUser(
  userId: string | null,
  traits?: Record<string, unknown>,
): void {
  if (typeof window === "undefined") return;
  if (!(posthog as unknown as { __loaded?: boolean }).__loaded) return;
  try {
    if (!userId) {
      posthog.reset();
      return;
    }
    posthog.identify(userId, traits);
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn("[posthog] identify failed", err);
  }
}

/**
 * Attach the current user to a group for cohort analytics. We use
 * group_type="tier" with the user's plan as the key (e.g. "free",
 * "pro", "business"), which lets dashboards filter "free-tier funnel"
 * without manually filtering on a person property each time.
 */
export function setPostHogTierGroup(tier: string | null): void {
  if (typeof window === "undefined") return;
  if (!(posthog as unknown as { __loaded?: boolean }).__loaded) return;
  if (!tier) return;
  try {
    posthog.group("tier", tier);
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn("[posthog] group failed", err);
  }
}

export function PostHogProvider({ children }: PostHogProviderProps) {
  const consent = useCookieConsent();
  useEffect(() => {
    // GDPR / ePrivacy: PostHog product analytics + session replay are
    // not strictly necessary, so we only initialize after the user
    // explicitly accepts the cookie banner. Declined / pending both
    // bail — no events, no distinct_id cookie.
    //
    // If consent flips from accepted → declined later (user revoked
    // via "Cookie preferences"), we call opt_out_capturing so any
    // already-loaded SDK stops sending events without a page reload.
    if (consent === "accepted") {
      initPostHog();
      try {
        if ((posthog as unknown as { __loaded?: boolean }).__loaded) {
          posthog.opt_in_capturing();
        }
      } catch {
        // SDK opt-in helper is missing on older versions — init does
        // the right thing on its own.
      }
    } else if (consent === "declined") {
      try {
        if ((posthog as unknown as { __loaded?: boolean }).__loaded) {
          posthog.opt_out_capturing();
        }
      } catch {
        // Same — older SDK versions might lack opt_out_capturing.
      }
    }
  }, [consent]);
  return (
    <>
      <Suspense fallback={null}>
        <PostHogPageView />
      </Suspense>
      {children}
    </>
  );
}
