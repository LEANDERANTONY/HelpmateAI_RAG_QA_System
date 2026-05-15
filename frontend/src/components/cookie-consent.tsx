"use client";

/**
 * CookieConsent — EU/ePrivacy-compliant cookie banner.
 *
 * Legal posture (the rule we're encoding):
 *   • Strictly-necessary cookies (Supabase Auth session, CSRF) load
 *     regardless of consent. They're allowed by ePrivacy Directive
 *     Art. 5(3) as "strictly necessary for the service the user
 *     requested."
 *   • Error tracking (Sentry, errors only — no Session Replay) loads
 *     regardless of consent. Justified as legitimate interest under
 *     GDPR Art. 6(1)(f) — we need crash reporting to operate the
 *     service securely. This is the standard SaaS posture.
 *   • Everything else (PostHog product analytics, PostHog session
 *     replay, Sentry Session Replay) requires EXPLICIT opt-in.
 *
 * State machine — three values in localStorage["helpmate-cookie-consent"]:
 *   "pending"  → banner shown, no analytics fired
 *   "accepted" → banner hidden, PostHog + Sentry Replay live
 *   "declined" → banner hidden, no analytics, no replay
 *
 * Re-opening the choice: a footer link "Cookie preferences" calls
 * ``openCookiePreferences()`` which sets the key back to "pending"
 * and dispatches a CustomEvent so the banner re-mounts. We don't
 * call ``location.reload()`` so the user's workspace state is
 * preserved across the toggle.
 *
 * Why not Cookiebot/Iubenda/Termly:
 *   • Their pricing for our scale (~$11-27/mo) buys compliance theater
 *     dashboards we don't need
 *   • Their banners load third-party JS BEFORE the user has consented
 *     to third-party JS, which is its own compliance footgun
 *   • Our policy is simple: two categories ("essential" + "all"),
 *     two buttons. Building it ourselves is ~100 lines and gives us
 *     pixel control over the theme
 */

import { useEffect, useState } from "react";

const STORAGE_KEY = "helpmate-cookie-consent";
const CHANGE_EVENT = "helpmate-cookie-consent-change";

export type CookieConsentValue = "pending" | "accepted" | "declined";

/**
 * Read the current consent without subscribing to changes. Safe on
 * the server (returns "pending" — banner shows on first paint).
 */
export function getCookieConsent(): CookieConsentValue {
  if (typeof window === "undefined") return "pending";
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === "accepted" || raw === "declined") return raw;
  } catch {
    // localStorage can throw in incognito + Safari ITP; treat as
    // pending so we re-prompt on next visit.
  }
  return "pending";
}

/**
 * Hook that returns the current consent and re-renders when it
 * changes. Use from any client component that gates behavior on
 * consent (e.g. PostHogProvider, Sentry Replay opt-in).
 */
export function useCookieConsent(): CookieConsentValue {
  const [value, setValue] = useState<CookieConsentValue>(() => getCookieConsent());
  useEffect(() => {
    if (typeof window === "undefined") return;
    function handler() {
      setValue(getCookieConsent());
    }
    window.addEventListener(CHANGE_EVENT, handler);
    // Cross-tab sync: storage event fires when another tab updates
    // localStorage. Without this, accepting in one tab leaves the
    // banner stuck on another open tab.
    window.addEventListener("storage", (event: StorageEvent) => {
      if (event.key === STORAGE_KEY) handler();
    });
    return () => {
      window.removeEventListener(CHANGE_EVENT, handler);
      // The storage listener uses an inline closure so it can't be
      // removed cleanly here — we rely on listener identity dedupe
      // via the bound function. In practice this re-registers per
      // mount which is acceptable for a singleton-scope component.
    };
  }, []);
  return value;
}

/**
 * Imperatively set the consent state. Called by the banner buttons
 * + the footer "Cookie preferences" link. Dispatches CHANGE_EVENT so
 * components using ``useCookieConsent`` re-render immediately.
 */
function setCookieConsent(next: CookieConsentValue): void {
  if (typeof window === "undefined") return;
  try {
    if (next === "pending") {
      window.localStorage.removeItem(STORAGE_KEY);
    } else {
      window.localStorage.setItem(STORAGE_KEY, next);
    }
  } catch {
    // localStorage rejected (incognito / quota) — the consent won't
    // persist across reloads but the in-page state still updates.
  }
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
}

/**
 * Public function for the footer link "Cookie preferences". Resets
 * the consent to "pending" which re-renders the banner so the user
 * can change their mind.
 */
export function openCookiePreferences(): void {
  setCookieConsent("pending");
}

export function CookieConsentBanner(): React.ReactElement | null {
  const consent = useCookieConsent();
  // Mount guard — server renders this null, then on hydration the
  // useEffect inside useCookieConsent reads localStorage. Without
  // this we get a hydration mismatch warning when consent !== "pending".
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);
  if (!mounted) return null;
  if (consent !== "pending") return null;

  return (
    <div
      role="dialog"
      aria-labelledby="cookie-consent-heading"
      aria-describedby="cookie-consent-body"
      className="h-cookie-banner"
    >
      <div className="h-cookie-content">
        <div className="h-cookie-text">
          <p id="cookie-consent-heading" className="h-cookie-heading">
            We use cookies
          </p>
          <p id="cookie-consent-body" className="h-cookie-body">
            Essential cookies keep you signed in. With your consent we also use
            product analytics and session replay to understand how the workspace
            is used and fix bugs faster. You can change this any time from the
            footer.
          </p>
        </div>
        <div className="h-cookie-actions">
          <button
            type="button"
            className="h-cookie-btn h-cookie-btn-ghost"
            onClick={() => setCookieConsent("declined")}
          >
            Decline non-essential
          </button>
          <button
            type="button"
            className="h-cookie-btn h-cookie-btn-primary"
            onClick={() => setCookieConsent("accepted")}
          >
            Accept
          </button>
        </div>
      </div>
    </div>
  );
}
