import { NextResponse } from "next/server";

import { createClient } from "@/lib/supabase/server";
import { isSupabaseConfigured } from "@/lib/supabase/config";

/** Validate that a redirect target is a local same-origin path.
 *
 *  `new URL(input, origin)` returns the absolute URL `input` if `input`
 *  is itself absolute, completely ignoring `origin`. That means an
 *  attacker can craft `/auth/callback?next=https://evil.com/phish` and
 *  redirect the user off-site immediately after sign-in — a classic
 *  open-redirect vector usable in phishing flows (Codex P2 finding
 *  on PR #4, May 2026).
 *
 *  Safe `next` values: must start with a single `/` and not begin with
 *  `//` (protocol-relative URL) or `/\` (Windows-style path that some
 *  browsers normalize to `//`). Anything else falls back to `/`.
 */
function sanitizeNextPath(rawNext: string | null | undefined): string {
  const fallback = "/";
  if (!rawNext || typeof rawNext !== "string") return fallback;
  if (!rawNext.startsWith("/")) return fallback;
  if (rawNext.startsWith("//") || rawNext.startsWith("/\\")) return fallback;
  return rawNext;
}

export async function GET(request: Request) {
  const requestUrl = new URL(request.url);
  const code = requestUrl.searchParams.get("code");
  const next = sanitizeNextPath(requestUrl.searchParams.get("next"));

  if (code && isSupabaseConfigured()) {
    const supabase = await createClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (error) {
      // The code exchange failed (expired / already-used / invalid code, or
      // a transient Supabase error). Don't drop the user back onto the app
      // silently signed-out — redirect with ?auth_error=1 so AuthErrorToast
      // surfaces a "sign-in failed" message instead of a no-op (L13).
      const errorUrl = new URL(next, requestUrl.origin);
      errorUrl.searchParams.set("auth_error", "1");
      return NextResponse.redirect(errorUrl);
    }
  }

  return NextResponse.redirect(new URL(next, requestUrl.origin));
}
