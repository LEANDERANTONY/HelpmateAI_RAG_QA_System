"use client";

// Lazy-loaded PDF.js facade.
//
// We import pdfjs-dist + its bundled viewer via dynamic import so the
// ~700KB of code only ships when the user actually enters Read Mode.
// Both modules are cached on the module object after first load — re-
// entering Read Mode doesn't re-import, just reuses the references.
//
// Worker hosting: a postinstall script copies
//   node_modules/pdfjs-dist/build/pdf.worker.min.mjs
// to
//   public/pdf.worker.min.mjs
// which Next serves as a static asset. GlobalWorkerOptions.workerSrc
// is set the first time we load the core module. See
// scripts/copy-pdfjs-worker.mjs for the why.
//
// Auth model:
//   • getDocument takes `httpHeaders` for every Range request PDF.js
//     issues against the file URL.
//   • We pull a fresh Supabase access token via getSession() (which
//     auto-refreshes when the token is within its expiry buffer).
//   • If the FIRST getDocument call rejects with 401, we explicitly
//     call refreshSession() and retry once. This handles the edge
//     case where the cached session is technically valid client-side
//     but the server rejected it (clock skew, server-side revocation).
//   • If PDF.js issues a Range request DURING streaming and gets 401
//     (token expired mid-read), PDF.js will reject the
//     getDocument().promise — we surface that as a "session expired,
//     reload" error. Long reads exceeding the 1hr token lifetime
//     aren't catastrophic, just need a page reload to refresh.

// Pull the runtime constructors as type-only imports so we can pin our
// facade's shape to whatever pdfjs-dist actually ships. Using `typeof` on
// the class re-exports gives us both the constructor signature and the
// instance type via InstanceType<typeof X> when callers need it.
import type * as PdfjsViewer from "pdfjs-dist/web/pdf_viewer.mjs";
import type { PDFDocumentProxy } from "pdfjs-dist";

import { createClient } from "@/lib/supabase/client";
import { isSupabaseConfigured } from "@/lib/supabase/config";

export type PdfjsModule = {
  getDocument: typeof import("pdfjs-dist").getDocument;
  EventBus: typeof PdfjsViewer.EventBus;
  PDFViewer: typeof PdfjsViewer.PDFViewer;
  PDFLinkService: typeof PdfjsViewer.PDFLinkService;
  PDFFindController: typeof PdfjsViewer.PDFFindController;
};

let cachedModule: PdfjsModule | null = null;
let loadPromise: Promise<PdfjsModule> | null = null;

const WORKER_SRC = "/pdf.worker.min.mjs";

export async function loadPdfjs(): Promise<PdfjsModule> {
  if (cachedModule) {
    return cachedModule;
  }
  if (loadPromise) {
    return loadPromise;
  }
  loadPromise = (async () => {
    // CRITICAL ordering: pdfjs-dist/web/pdf_viewer.mjs destructures
    // AbortException (and other symbols) from `globalThis.pdfjsLib` at
    // module evaluation time. If we Promise.all the two imports, the
    // viewer module can evaluate before the core sets up the global,
    // and the destructure throws:
    //
    //   Cannot destructure property 'AbortException' of
    //   'globalThis.pdfjsLib' as it is undefined.
    //
    // Dev hides this because Turbopack happens to satisfy the imports
    // in an order where pdfjsLib is in place; production's chunk-split
    // module-eval order exposes the race. Fix: import core first,
    // assign the global, THEN import the viewer.
    const core = await import("pdfjs-dist");
    // The viewer's expected global is `pdfjsLib` on `globalThis`.
    // Type assertion because TS doesn't know about this contract.
    (globalThis as unknown as { pdfjsLib: typeof core }).pdfjsLib = core;
    core.GlobalWorkerOptions.workerSrc = WORKER_SRC;
    const viewer = await import("pdfjs-dist/web/pdf_viewer.mjs");
    const merged: PdfjsModule = {
      getDocument: core.getDocument,
      EventBus: viewer.EventBus,
      PDFViewer: viewer.PDFViewer,
      PDFLinkService: viewer.PDFLinkService,
      PDFFindController: viewer.PDFFindController,
    };
    cachedModule = merged;
    return merged;
  })();
  return loadPromise;
}

/**
 * Read the current access token from Supabase. Returns `null` when
 * Supabase isn't configured (e.g. local dev without env vars) or when
 * there's no active session. The supabase client auto-refreshes tokens
 * that are near expiry on every `getSession()` call.
 */
async function getAccessToken(): Promise<string | null> {
  if (typeof window === "undefined" || !isSupabaseConfigured()) {
    return null;
  }
  const supabase = createClient();
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}

/**
 * Force-refresh the Supabase session. Used after a 401 from the
 * backend, in case our cached session is stale on the server side.
 * Returns the refreshed token or null on failure.
 */
async function refreshAccessToken(): Promise<string | null> {
  if (typeof window === "undefined" || !isSupabaseConfigured()) {
    return null;
  }
  const supabase = createClient();
  const { data } = await supabase.auth.refreshSession();
  return data.session?.access_token ?? null;
}

/**
 * Custom error subclass so the viewer can branch on what kind of
 * failure happened — auth, missing file, legacy DOCX with no PDF
 * rendition, generic load failure.
 */
export class PdfLoadError extends Error {
  constructor(
    message: string,
    public readonly kind:
      | "auth"
      | "missing"
      | "needs-rendition"
      | "transport"
      | "parse"
      | "unknown",
    public readonly status?: number,
  ) {
    super(message);
    this.name = "PdfLoadError";
  }
}

type ResponseException = {
  name: "UnexpectedResponseException" | "MissingPDFException" | string;
  status?: number;
  message?: string;
};

function categorizeError(err: unknown): PdfLoadError {
  const candidate = err as ResponseException | undefined;
  if (candidate && typeof candidate === "object") {
    if (candidate.name === "UnexpectedResponseException") {
      const status = candidate.status ?? 0;
      if (status === 401 || status === 403) {
        return new PdfLoadError("Session expired", "auth", status);
      }
      if (status === 404) {
        return new PdfLoadError("Source no longer available", "missing", status);
      }
      if (status === 410) {
        // Workspace TTL expired (backend _require_document_for_user). Reuse the
        // 'missing' banner ("workspace may have expired — reload to start
        // fresh") rather than the generic transport error, whose "try closing
        // and reopening Read Mode" advice just re-hits 410 in a loop (M17).
        return new PdfLoadError("Source no longer available", "missing", status);
      }
      if (status === 415) {
        return new PdfLoadError(
          "Viewer needs a PDF rendition",
          "needs-rendition",
          status,
        );
      }
      return new PdfLoadError(
        `Document fetch failed (HTTP ${status})`,
        "transport",
        status,
      );
    }
    if (candidate.name === "MissingPDFException") {
      return new PdfLoadError("Source file is missing", "missing");
    }
  }
  return new PdfLoadError(
    err instanceof Error ? err.message : "Couldn't load source PDF",
    "unknown",
  );
}

/**
 * Load a PDF document from the backend, attaching the Supabase bearer
 * token to every request. Retries once on 401 after refreshing the
 * session.
 *
 * Returns the PDFDocumentProxy when successful; callers should call
 * `.destroy()` on the returned proxy during cleanup.
 */
export async function loadPdfDocument(url: string): Promise<PDFDocumentProxy> {
  const pdfjs = await loadPdfjs();

  const fetchWithToken = async (token: string | null) => {
    const headers: Record<string, string> = {};
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
    return pdfjs.getDocument({
      url,
      httpHeaders: headers,
      // withCredentials would also send Supabase cookies, but our auth
      // is bearer-only — explicit token in the header is enough.
      withCredentials: false,
      // PDFs are arbitrary user uploads. Disable the worker's eval/Function
      // path used for some embedded font/JS programs — the documented pdfjs
      // hardening flag for untrusted input, with no impact on text/highlight
      // rendering (M3).
      isEvalSupported: false,
    }).promise;
  };

  const initialToken = await getAccessToken();
  try {
    return await fetchWithToken(initialToken);
  } catch (firstError) {
    const categorized = categorizeError(firstError);
    if (categorized.kind !== "auth") {
      throw categorized;
    }
    // 401/403 path: refresh and retry once. If the refresh itself
    // fails or the retry also 401s, surface as auth error.
    const refreshed = await refreshAccessToken();
    if (!refreshed) {
      throw categorized;
    }
    try {
      return await fetchWithToken(refreshed);
    } catch (secondError) {
      throw categorizeError(secondError);
    }
  }
}
