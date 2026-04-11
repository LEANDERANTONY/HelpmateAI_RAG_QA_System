import type {
  BenchmarkResponse,
  DocumentBundleResponse,
  HealthResponse,
  SampleDocument,
  StarterQuestionsResponse,
} from "@/lib/api-types";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, init);
  if (!response.ok) {
    const fallbackMessage = `Request failed with status ${response.status}`;
    let message = fallbackMessage;
    try {
      const payload = (await response.json()) as { detail?: string };
      message = payload.detail ?? fallbackMessage;
    } catch {
      // Ignore JSON parsing failures and fall back to the generic message.
    }
    throw new Error(message);
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

export async function uploadDocument(file: File) {
  const formData = new FormData();
  formData.append("file", file);
  return request<DocumentBundleResponse>("/documents/upload", {
    method: "POST",
    body: formData,
  });
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

export { API_BASE_URL };
