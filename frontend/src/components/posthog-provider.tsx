"use client";

/**
 * PostHogProvider — initializes posthog-js on first client render and
 * keeps it tied to the Supabase user across navigation.
 *
 * Wired in ``src/app/layout.tsx`` so every route (workspace, landing,
 * auth) emits page-view + autocapture events into the same project.
 *
 * Identity flow:
 *   1. On consent, ``initPostHog`` dynamically imports + inits the SDK
 *      once. We use ``persistence: "localStorage+cookie"`` so the
 *      distinct_id survives a hard reload but a logged-out user is reset
 *      to anonymous on explicit ``posthog.reset()``.
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
import type { PostHog } from "posthog-js";

import { useCookieConsent } from "@/components/cookie-consent";

type PostHogProviderProps = {
  children: React.ReactNode;
};

// posthog-js (~50-60KB gzipped, with session replay) is dynamically imported
// inside initPostHog — which only runs AFTER the user accepts the cookie
// banner — so the SDK never lands in the landing/initial bundle that every
// visitor downloads (M11). Every helper below guards on isLoaded().
let posthog: PostHog | null = null;

function isLoaded(): boolean {
  return Boolean(posthog && (posthog as unknown as { __loaded?: boolean }).__loaded);
}

async function initPostHog(): Promise<void> {
  const key = process.env.NEXT_PUBLIC_POSTHOG_KEY;
  if (!key) return;
  if (typeof window === "undefined") return;
  // Re-init guard. React StrictMode mounts the effect twice in dev,
  // and a SPA navigation through layout.tsx would re-run useEffect on
  // every route transition without this.
  if (isLoaded()) return;
  try {
    // Lazy-load the SDK only on consent, keeping it out of the initial bundle.
    posthog = (await import("posthog-js")).default;
    posthog.init(key, {
      api_host: process.env.NEXT_PUBLIC_POSTHOG_HOST || "https://eu.i.posthog.com",
      // Manual page-view capture via PostHogPageView below. The built-in
      // capture_pageview path listens for popstate/hashchange which the App
      // Router does NOT fire on navigation — it would emit zero pageviews.
      capture_pageview: false,
      // Capture form submits + clicks. Plenty for funnel building.
      autocapture: true,
      // Session replay is in the PostHog free tier (5K replays/month).
      session_recording: {
        maskAllInputs: true,
      },
      // Honor Do-Not-Track.
      respect_dnt: true,
      persistence: "localStorage+cookie",
    });
    // Register ``product: "helpmate"`` so every event carries the tag (the
    // PostHog free tier caps at 1 project per org; HelpmateAI + AI Job Agent
    // share it and split via ``where product = '...'``).
    posthog.register({ product: "helpmate" });
  } catch (err) {
    // Swallow — analytics must never break the page.
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
    if (!isLoaded()) return;
    const url = pathname + (searchParams?.toString() ? `?${searchParams.toString()}` : "");
    posthog?.capture("$pageview", { $current_url: window.location.origin + url });
  }, [pathname, searchParams]);
  return null;
}

/**
 * Tie the current PostHog session to a Supabase user id. Safe to call
 * with the same id on every render — posthog-js dedupes identify
 * calls internally. Passing ``null`` resets the session to anonymous
 * (logout flow). No-op until the SDK has loaded (consent granted).
 */
export function identifyPostHogUser(
  userId: string | null,
  traits?: Record<string, unknown>,
): void {
  if (typeof window === "undefined") return;
  if (!isLoaded()) return;
  try {
    if (!userId) {
      posthog?.reset();
      return;
    }
    posthog?.identify(userId, traits);
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn("[posthog] identify failed", err);
  }
}

/**
 * Attach the current user to a group for cohort analytics
 * (group_type="tier"). No-op until the SDK has loaded.
 */
export function setPostHogTierGroup(tier: string | null): void {
  if (typeof window === "undefined") return;
  if (!isLoaded()) return;
  if (!tier) return;
  try {
    posthog?.group("tier", tier);
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn("[posthog] group failed", err);
  }
}

/**
 * Capture a named funnel event. A no-op until the SDK has loaded
 * (consent pending/declined, key unset, ad blocker), so it respects
 * the cookie-consent gate. Use for explicit funnel steps autocapture
 * can't reliably key on (M23).
 */
export function capturePostHogEvent(
  event: string,
  properties?: Record<string, unknown>,
): void {
  if (typeof window === "undefined") return;
  if (!isLoaded()) return;
  try {
    posthog?.capture(event, properties);
  } catch (err) {
    console.warn("[posthog] capture failed", err);
  }
}

export function PostHogProvider({ children }: PostHogProviderProps) {
  const consent = useCookieConsent();
  useEffect(() => {
    // GDPR / ePrivacy: PostHog analytics + session replay are not strictly
    // necessary, so we only load + initialize after the user accepts the
    // cookie banner. Declined / pending both bail — no events, no cookie.
    if (consent === "accepted") {
      void initPostHog().then(() => {
        try {
          if (isLoaded()) posthog?.opt_in_capturing();
        } catch {
          // Older SDK versions might lack opt_in_capturing — init handles it.
        }
      });
    } else if (consent === "declined") {
      // If consent flips accepted → declined later, stop an already-loaded
      // SDK from sending without a reload.
      try {
        if (isLoaded()) posthog?.opt_out_capturing();
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
