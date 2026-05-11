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

export async function askQuestion(documentId: string, question: string) {
  return request<{ answer: import("@/lib/api-types").AnswerResult }>("/qa", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ document_id: documentId, question }),
  });
}

export { API_BASE_URL, UPLOAD_API_BASE_URL };
