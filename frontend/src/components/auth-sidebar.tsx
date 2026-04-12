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
  const [collapsed, setCollapsed] = useState(false);
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
    <aside
      className={`workspace-sidebar ${collapsed ? "workspace-sidebar-collapsed" : ""}`}
    >
      <div className="workspace-sidebar-shell">
        <button
          aria-expanded={!collapsed}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className="workspace-sidebar-toggle"
          onClick={() => setCollapsed((current) => !current)}
          type="button"
        >
          <span />
          <span />
          <span />
        </button>

        <div className="workspace-sidebar-brand">
          <div className="workspace-sidebar-brand-dot" />
          {!collapsed ? (
            <div>
              <p>HelpmateAI</p>
              <span>Grounded document workflow</span>
            </div>
          ) : null}
        </div>

        {!collapsed ? (
          <>
            <div className="workspace-sidebar-card">
              <p className="eyebrow">Account</p>
              <h2 className="workspace-sidebar-title">{accountLabel}</h2>
              <p className="workspace-sidebar-copy">
                {user
                  ? "Your session unlocks the private workspace flow so later we can enforce per-user document retention."
                  : authEnabled
                    ? "Sign in with Google from the workspace rail to unlock uploads, indexing, and personal document state."
                    : "Add the Supabase frontend env vars to enable Google sign-in in this workspace."}
              </p>

              {user?.avatarUrl ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  alt={user.displayName || user.email || "User avatar"}
                  className="workspace-sidebar-avatar"
                  src={user.avatarUrl}
                />
              ) : null}

              {user ? (
                <button
                  className="secondary-button mt-5 w-full px-4 py-3"
                  disabled={pendingAction === "signout"}
                  onClick={handleSignOut}
                  type="button"
                >
                  {pendingAction === "signout" ? "Signing out..." : "Sign out"}
                </button>
              ) : (
                <button
                  className="primary-button mt-5 w-full px-4 py-3"
                  disabled={pendingAction === "signin" || !authEnabled}
                  onClick={handleGoogleSignIn}
                  type="button"
                >
                  {pendingAction === "signin"
                    ? "Redirecting..."
                    : "Continue with Google"}
                </button>
              )}

              {error ? (
                <p className="workspace-sidebar-error">{error}</p>
              ) : null}
            </div>

            <div className="workspace-sidebar-card">
              <p className="eyebrow">Workflow</p>
              <ul className="workspace-sidebar-stats">
                {workflowStates.map((item) => (
                  <li className="workspace-sidebar-stat" key={item.label}>
                    <span>{item.label}</span>
                    <strong>{item.value}</strong>
                  </li>
                ))}
              </ul>
            </div>
          </>
        ) : null}
      </div>
    </aside>
  );
}
