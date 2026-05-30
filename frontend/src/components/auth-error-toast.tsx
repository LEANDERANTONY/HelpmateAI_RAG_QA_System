"use client";

import { Suspense, useEffect, useRef } from "react";
import { useSearchParams } from "next/navigation";

import { notifyError } from "@/lib/toast";

/**
 * Surfaces a sign-in error toast when the OAuth callback redirected here
 * with ?auth_error=1 (L13). The /auth/callback route appends that flag when
 * exchangeCodeForSession fails, instead of silently dropping the user back
 * onto the app signed-out with no explanation. Toast once, then strip the
 * flag from the address bar so a reload or shared link doesn't replay it.
 */
function AuthErrorToastInner(): null {
  const searchParams = useSearchParams();
  const shown = useRef(false);

  useEffect(() => {
    if (shown.current) return;
    if (searchParams.get("auth_error") !== "1") return;
    shown.current = true;
    notifyError(
      "Sign-in failed",
      "We couldn't complete sign-in. Please try again.",
    );
    // Strip ?auth_error from the address bar so a reload doesn't re-toast.
    const url = new URL(window.location.href);
    url.searchParams.delete("auth_error");
    window.history.replaceState(null, "", url.toString());
  }, [searchParams]);

  return null;
}

export function AuthErrorToast() {
  // useSearchParams must sit under a Suspense boundary in the App Router,
  // or it opts the whole tree out of static rendering.
  return (
    <Suspense fallback={null}>
      <AuthErrorToastInner />
    </Suspense>
  );
}
