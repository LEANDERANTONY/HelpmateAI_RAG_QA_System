"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { openCookiePreferences } from "@/components/cookie-consent";
import { capturePostHogEvent } from "@/components/posthog-provider";
import type { AuthUserSummary } from "@/lib/auth";
import type { WorkspaceQuotaResponse } from "@/lib/api-types";
import { createClient } from "@/lib/supabase/client";
import { isSupabaseConfigured } from "@/lib/supabase/config";

type AuthSidebarProps = {
  user: AuthUserSummary | null;
  // Tier + per-month usage snapshot from GET /workspace/quota.
  // Optional so the sign-in / signed-out states still render
  // without a quota fetch in flight. When present, the popover
  // surfaces the plan label + remaining quota so users see their
  // entitlement directly rather than having to inspect the Premium
  // pill near the ask box.
  quotaSnapshot?: WorkspaceQuotaResponse | null;
};

/** Capitalize the lowercase tier string returned by the backend
 *  (`"free" | "pro" | "business"`) for display. Falls back to a
 *  generic capitalize for any future tier so a new "enterprise"
 *  string still renders sanely. */
function formatTier(tier: string | null | undefined): string {
  const normalized = (tier || "").trim().toLowerCase();
  switch (normalized) {
    case "free":
      return "Free";
    case "pro":
      return "Pro";
    case "business":
      return "Business";
    case "internal":
      return "Internal";
    case "admin":
      return "Admin";
    default:
      if (!normalized) return "Free";
      return normalized.charAt(0).toUpperCase() + normalized.slice(1);
  }
}

/** "12 of 50 used" / "Unlimited" rendering for a quota counter.
 *  ``limit === -1`` is the unbounded sentinel from the backend
 *  (Business tier on most counters); render "Unlimited" with the
 *  used count tucked behind it so a Business user still sees their
 *  activity without a denominator that would otherwise read as
 *  "X of -1 used". */
function formatQuotaLine(used: number, limit: number): string {
  if (limit < 0) {
    return used > 0 ? `Unlimited (${used} used)` : "Unlimited";
  }
  const safeUsed = Math.max(0, Math.min(used, limit));
  return `${safeUsed} of ${limit} used`;
}

export function AuthSidebar({ user, quotaSnapshot }: AuthSidebarProps) {
  const authEnabled = isSupabaseConfigured();
  const [pendingAction, setPendingAction] = useState<"signin" | "signout" | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  const accountLabel = useMemo(() => {
    if (!user) {
      return "Guest workspace";
    }

    return user.displayName || user.email || "Signed in";
  }, [user]);

  const accountInitial = useMemo(() => {
    const seed = user?.displayName || user?.email || "H";
    return seed.trim().charAt(0).toUpperCase();
  }, [user]);

  async function handleGoogleSignIn() {
    setError(null);
    setPendingAction("signin");
    try {
      if (!authEnabled) {
        throw new Error(
          "Supabase auth is not configured yet. Add NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY to Vercel and local env files.",
        );
      }
      capturePostHogEvent("sign_in_started", { provider: "google" });
      const supabase = createClient();
      const origin = window.location.origin;
      const { error: signInError } = await supabase.auth.signInWithOAuth({
        provider: "google",
        options: {
          redirectTo: `${origin}/auth/callback?next=/`,
        },
      });
      if (signInError) {
        throw signInError;
      }
    } catch (signInError) {
      setPendingAction(null);
      setError(
        signInError instanceof Error
          ? signInError.message
          : "Google sign-in could not be started.",
      );
    }
  }

  async function handleSignOut() {
    setError(null);
    setPendingAction("signout");
    try {
      const supabase = createClient();
      const { error: signOutError } = await supabase.auth.signOut();
      if (signOutError) {
        throw signOutError;
      }
      router.refresh();
    } catch (signOutError) {
      setPendingAction(null);
      setError(
        signOutError instanceof Error
          ? signOutError.message
          : "Sign-out failed unexpectedly.",
      );
    }
  }

  const sessionDescription = user
    ? user.email || "Your account is connected for private workspace access."
    : "Open Google sign-in to access uploads, indexing, and your saved workspace state.";

  return (
    <section className="auth-inline-panel">
      <p className="h-eyebrow">Account</p>
      <div className="auth-inline-identity">
        {user ? (
          <span className="auth-inline-avatar">{accountInitial}</span>
        ) : (
          <img
            alt=""
            aria-hidden="true"
            className="auth-inline-avatar auth-inline-avatar-logo"
            height={30}
            src="/brand/helpmate-icon.svg"
            width={30}
          />
        )}
        <h2>{accountLabel}</h2>
      </div>
      <p className="auth-inline-copy">
        {user
          ? "Signed in and ready to work with your active document workspace."
          : authEnabled
            ? "Sign in with Google to unlock uploads, indexing, and saved workspace access."
            : "Connect the Supabase frontend env vars to enable Google sign-in here."}
      </p>
      <div className="auth-inline-actions">
        {user ? (
          <button
            className="auth-inline-button"
            disabled={pendingAction === "signout"}
            onClick={handleSignOut}
            type="button"
          >
            {pendingAction === "signout" ? "Signing out..." : "Sign out"}
          </button>
        ) : (
          <button
            className="auth-inline-button auth-inline-primary"
            disabled={pendingAction === "signin" || !authEnabled}
            onClick={handleGoogleSignIn}
            type="button"
          >
            {pendingAction === "signin" ? "Redirecting..." : "Continue with Google"}
          </button>
        )}
      </div>

      {/* Plan + quota summary. Only renders for signed-in users with a
          fetched quota snapshot. Earlier the popover surfaced neither
          the tier label nor the per-month counters, so users had to
          inspect the Premium pill near the ask box to figure out their
          entitlement. Each counter shows ``X of Y used`` (or
          ``Unlimited`` when the backend's -1 sentinel applies on
          Business). */}
      {user && quotaSnapshot ? (
        <div className="auth-inline-meta">
          <div>
            <p className="h-eyebrow">Plan</p>
            <h3>{formatTier(quotaSnapshot.tier)}</h3>
            <p>
              Questions:{" "}
              {formatQuotaLine(
                quotaSnapshot.questions.used,
                quotaSnapshot.questions.limit,
              )}
            </p>
            <p>
              Premium answers:{" "}
              {formatQuotaLine(
                quotaSnapshot.premium.used,
                quotaSnapshot.premium.limit,
              )}
            </p>
            <p>
              Documents:{" "}
              {formatQuotaLine(
                quotaSnapshot.documents.used,
                quotaSnapshot.documents.limit,
              )}
            </p>
          </div>
        </div>
      ) : null}

      <div className="auth-inline-meta">
        <div>
          <p className="h-eyebrow">Session</p>
          <h3>{user ? "Google account connected" : "Ready for sign-in"}</h3>
          <p>{sessionDescription}</p>
        </div>
      </div>

      {error ? <p className="auth-inline-error">{error}</p> : null}

      {/* Cookie preferences re-opener. Lives in the auth sidebar
          (not just the landing footer) so workspace users who never
          see the footer can still revoke consent without hunting for
          it. The button reads as a link to match the visual weight
          of the surrounding auth chrome. */}
      <button
        type="button"
        className="auth-inline-cookie-link"
        onClick={openCookiePreferences}
      >
        Cookie preferences
      </button>
    </section>
  );
}
