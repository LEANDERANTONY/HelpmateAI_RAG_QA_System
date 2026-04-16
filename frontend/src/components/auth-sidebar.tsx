"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import type { AuthUserSummary } from "@/lib/auth";
import { createClient } from "@/lib/supabase/client";
import { isSupabaseConfigured } from "@/lib/supabase/config";

type AuthSidebarProps = {
  user: AuthUserSummary | null;
};

const workflowStates = [
  { label: "Auth", value: "Google session" },
  { label: "Storage", value: "Supabase + Chroma" },
  { label: "Mode", value: "One document workspace" },
];

export function AuthSidebar({ user }: AuthSidebarProps) {
  const authEnabled = isSupabaseConfigured();
  const [expanded, setExpanded] = useState(false);
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

  return (
    <section className="auth-inline-panel">
      <div className="auth-inline-summary">
        <div className="auth-inline-lead">
          <button
            aria-expanded={expanded}
            aria-label={expanded ? "Collapse account panel" : "Expand account panel"}
            className="auth-inline-toggle"
            onClick={() => setExpanded((current) => !current)}
            type="button"
          >
            <span />
            <span />
            <span />
          </button>

          <div className="auth-inline-heading">
            <p className="eyebrow">Account</p>
            <div className="auth-inline-identity">
              <span className="auth-inline-avatar">{accountInitial}</span>
              <h2>{accountLabel}</h2>
            </div>
          </div>
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
              className="secondary-button px-4 py-3"
              disabled={pendingAction === "signout"}
              onClick={handleSignOut}
              type="button"
            >
              {pendingAction === "signout" ? "Signing out..." : "Sign out"}
            </button>
          ) : (
            <button
              className="primary-button px-4 py-3"
              disabled={pendingAction === "signin" || !authEnabled}
              onClick={handleGoogleSignIn}
              type="button"
            >
              {pendingAction === "signin" ? "Redirecting..." : "Continue with Google"}
            </button>
          )}
        </div>
      </div>

      {expanded ? (
        <div className="auth-inline-details">
          <div className="auth-inline-details-card">
            <p className="eyebrow">Session</p>
            <h3>{user ? "Google account connected" : "Ready for sign-in"}</h3>
            <p>
              {user
                ? user.email || "Your account is connected for private workspace access."
                : "Open Google sign-in to access uploads, indexing, and your saved workspace state."}
            </p>
          </div>

          {workflowStates.map((item) => (
            <div className="auth-inline-details-card" key={item.label}>
              <p className="eyebrow">{item.label}</p>
              <h3>{item.value}</h3>
              <p>
                {item.label === "Auth"
                  ? "Session-based access for a private document workflow."
                  : item.label === "Storage"
                    ? "Cloud-backed document state and vector retrieval."
                    : "A focused workspace tuned for one active document at a time."}
              </p>
            </div>
          ))}
        </div>
      ) : null}

      {error ? <p className="auth-inline-error">{error}</p> : null}
    </section>
  );
}
