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
 *   2. A small Supabase listener (added in ``use-posthog-identity``)
 *      pairs the anonymous PostHog id to the authenticated user id
 *      on login and resets on logout.
 *
 * Failure modes:
 *   • NEXT_PUBLIC_POSTHOG_KEY unset → the provider renders children
 *     unchanged and emits no events. Useful for local dev.
 *   • posthog-js fails to load (network blocked, ad blocker) → the
 *     ``try/catch`` around init keeps a broken analytics import from
 *     blocking the workspace render.
 */

import { useEffect } from "react";
import posthog from "posthog-js";

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
      // Manual page-view capture from the App Router listener (added
      // in a follow-up patch) is more reliable than autocapture because
      // the App Router does not fire the legacy "popstate" event the
      // SDK listens for. Setting capture_pageview to false here avoids
      // the SDK and our listener double-firing.
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

export function PostHogProvider({ children }: PostHogProviderProps) {
  useEffect(() => {
    initPostHog();
  }, []);
  return <>{children}</>;
}
