import type {
  BenchmarkResponse,
  CurrentWorkspaceResponse,
  DocumentBundleResponse,
  HealthResponse,
  SampleDocument,
  StarterQuestionsResponse,
} from "@/lib/api-types";
import { ApiError, isRetriableStatus } from "@/lib/api-errors";
import { createClient } from "@/lib/supabase/client";
import { isSupabaseConfigured } from "@/lib/supabase/config";

const PRODUCTION_API_BASE_URL = "https://api.helpmateai.xyz";

function resolveApiBaseUrl(value: string | undefined) {
  if (value && value !== "/api") {
    return value;
  }
  return process.env.NODE_ENV === "production" ? PRODUCTION_API_BASE_URL : "/api";
}

const API_BASE_URL = resolveApiBaseUrl(process.env.NEXT_PUBLIC_API_BASE_URL);
const UPLOAD_API_BASE_URL =
  resolveApiBaseUrl(process.env.NEXT_PUBLIC_UPLOAD_API_BASE_URL) ?? API_BASE_URL;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return requestAgainst<T>(API_BASE_URL, path, init);
}

async function requestAgainst<T>(
  baseUrl: string,
  path: string,
  init?: RequestInit,
): Promise<T> {
  const headers = new Headers(init?.headers);
  if (typeof window !== "undefined" && isSupabaseConfigured()) {
    const supabase = createClient();
    const {
      data: { session },
    } = await supabase.auth.getSession();
    const token = session?.access_token;
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }
  }

  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, {
      ...init,
      headers,
    });
  } catch (fetchError) {
    if (fetchError instanceof DOMException && fetchError.name === "AbortError") {
      throw fetchError;
    }
    throw new ApiError(0, "", true);
  }
  if (!response.ok) {
    let detail = "";
    try {
      const payload = (await response.json()) as { detail?: string };
      if (typeof payload.detail === "string") {
        detail = payload.detail;
      }
    } catch {
      // Body wasn't JSON. Leave detail empty so callers can use a friendly fallback.
    }
    const retryAfterHeader = response.headers.get("retry-after");
    let retryAfterSeconds: number | null = null;
    if (retryAfterHeader) {
      const trimmed = retryAfterHeader.trim();
      if (/^\d+$/.test(trimmed)) {
        retryAfterSeconds = parseInt(trimmed, 10);
      } else {
        const dateMs = Date.parse(trimmed);
        if (!Number.isNaN(dateMs)) {
          const diff = Math.ceil((dateMs - Date.now()) / 1000);
          if (diff > 0) {
            retryAfterSeconds = diff;
          }
        }
      }
    }
    throw new ApiError(
      response.status,
      detail,
      isRetriableStatus(response.status),
      retryAfterSeconds,
    );
  }
  return (await response.json()) as T;
}

export async function getHealth() {
  return request<HealthResponse>("/health");
}

export async function getLatestBenchmarks() {
  return request<BenchmarkResponse>("/benchmarks/latest");
}

export async function getSampleDocuments() {
  return request<SampleDocument[]>("/samples");
}

export async function loadSampleDocument(sampleSlug: string) {
  return request<DocumentBundleResponse>(
    `/samples/${encodeURIComponent(sampleSlug)}/load`,
    {
      method: "POST",
    },
  );
}

export async function getCurrentWorkspace() {
  return request<CurrentWorkspaceResponse>("/workspace/current");
}

export async function uploadDocument(file: File) {
  const formData = new FormData();
  formData.append("file", file);
  return requestAgainst<DocumentBundleResponse>(
    UPLOAD_API_BASE_URL,
    "/documents/upload",
    {
    method: "POST",
    body: formData,
    },
  );
}

export async function buildIndex(documentId: string) {
  return request<DocumentBundleResponse>(`/documents/${documentId}/index`, {
    method: "POST",
  });
}

export async function getStarterQuestions(documentId: string) {
  return request<StarterQuestionsResponse>(`/documents/${documentId}/starters`);
}

export async function askQuestion(
  documentId: string,
  question: string,
  options: { premium?: boolean } = {},
) {
  // `premium` opts the request into the paid-tier gpt-5.5 model.
  // The backend re-validates tier eligibility — sending premium=true
  // as a free user returns 402 premium_unavailable.
  const body: { document_id: string; question: string; premium?: boolean } = {
    document_id: documentId,
    question,
  };
  if (options.premium) {
    body.premium = true;
  }
  return request<{ answer: import("@/lib/api-types").AnswerResult }>("/qa", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
}

export async function getWorkspaceQuota() {
  // Per-user quota snapshot for the current calendar month. Read on
  // workspace mount + after each /qa response so the Premium toggle's
  // used/limit indicator stays in sync. Cheap call (one counter read
  // + one doc-count scan) — safe to refetch frequently.
  return request<import("@/lib/api-types").WorkspaceQuotaResponse>("/workspace/quota");
}

// ---------------------------------------------------------------------------
// Lemon Squeezy checkout + customer portal helpers.
//
// Env vars (NEXT_PUBLIC_ prefix so Next.js inlines them into the JS
// bundle at build time):
//   NEXT_PUBLIC_LEMONSQUEEZY_STORE_ID — subdomain piece. "" disables.
//   NEXT_PUBLIC_LEMONSQUEEZY_PRODUCT_VARIANT_PRO     — pro variant uuid
//   NEXT_PUBLIC_LEMONSQUEEZY_PRODUCT_VARIANT_BUSINESS — business uuid
//
// When any of these are empty (the integration isn't live on this
// deploy), getCheckoutUrl returns "" so the caller can render the CTA
// as "Coming soon" / disabled.
// ---------------------------------------------------------------------------

const LEMONSQUEEZY_STORE_ID =
  process.env.NEXT_PUBLIC_LEMONSQUEEZY_STORE_ID ?? "";
const LEMONSQUEEZY_VARIANT_PRO =
  process.env.NEXT_PUBLIC_LEMONSQUEEZY_PRODUCT_VARIANT_PRO ?? "";
const LEMONSQUEEZY_VARIANT_BUSINESS =
  process.env.NEXT_PUBLIC_LEMONSQUEEZY_PRODUCT_VARIANT_BUSINESS ?? "";

/** True when the LS env vars are populated. Pricing CTAs gate on this
 *  to render "Coming soon" copy when the integration hasn't shipped
 *  to this deploy yet. */
export function isLemonSqueezyEnabled(): boolean {
  return Boolean(
    LEMONSQUEEZY_STORE_ID &&
      (LEMONSQUEEZY_VARIANT_PRO || LEMONSQUEEZY_VARIANT_BUSINESS),
  );
}

/** Build a Lemon Squeezy hosted checkout URL for a tier, bound to the
 *  supplied Supabase user_id via checkout[custom][user_id]. The
 *  webhook handler reads that field out of meta.custom_data on
 *  subscription_created and writes the matching subscriptions row.
 *
 *  Also passes `checkout[success_url]` so the user lands back on the
 *  app with `?ls_checkout=success` — the workspace mount effect
 *  refreshes the quota snapshot on that param so the tier badge
 *  updates immediately after a successful payment.
 *
 *  Returns "" when the integration isn't configured on this build.
 *  The pricing CTA renders "Coming soon" in that branch. */
export function getCheckoutUrl(
  tier: "pro" | "business",
  userId: string,
): string {
  if (!LEMONSQUEEZY_STORE_ID) return "";
  const variant =
    tier === "pro"
      ? LEMONSQUEEZY_VARIANT_PRO
      : LEMONSQUEEZY_VARIANT_BUSINESS;
  if (!variant) return "";
  // Lemon Squeezy hosted-checkout URL path is `/checkout/buy/<variant>`
  // per the official docs. An earlier draft of this scaffold used
  // `/buy/<variant>` which is a non-checkout endpoint and would have
  // broken every paid conversion at launch (Codex P1 finding on
  // PR #4, May 2026).
  const base = `https://${LEMONSQUEEZY_STORE_ID}.lemonsqueezy.com/checkout/buy/${variant}`;
  if (!userId) return base;
  // LS expects checkout fields as URL-encoded query params; the
  // bracket syntax is the documented form for nested fields.
  const query = new URLSearchParams();
  query.set("checkout[custom][user_id]", userId);
  // Per-checkout success URL override. Build off the current origin
  // so dev / staging / prod each land on themselves; the LS
  // dashboard's default success URL is still honored when this
  // parameter isn't sent.
  if (typeof window !== "undefined" && window.location?.origin) {
    const successUrl = new URL(window.location.origin);
    // HelpmateAI's workspace IS the app root, so default the
    // success-return path to `/` rather than `/workspace`. Preserve
    // the current path so a click from the landing page returns to
    // the landing page.
    successUrl.pathname = window.location.pathname || "/";
    successUrl.searchParams.set("ls_checkout", "success");
    query.set("checkout[success_url]", successUrl.toString());
  }
  return `${base}?${query.toString()}`;
}

/** Hit the backend `/billing/portal` route to mint a one-time LS
 *  customer portal URL. The portal lets the user update card details,
 *  cancel, or resume the subscription. Caller typically window.locations
 *  to the returned url after wrapping the call in a try/catch + toast.
 *
 *  Throws on 401 (sign in), 404 (no subscription), 503 (LS not
 *  configured), 502 (LS upstream error). */
export async function getCustomerPortalUrl(): Promise<{ url: string }> {
  return request<{ url: string }>("/billing/portal", {
    method: "POST",
  });
}

export { API_BASE_URL, UPLOAD_API_BASE_URL };
