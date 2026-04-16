export type DocumentRecord = {
  document_id: string;
  file_name: string;
  file_type: string;
  source_path: string;
  fingerprint: string;
  char_count: number;
  page_count: number | null;
  metadata: Record<string, unknown>;
};

export type IndexRecord = {
  document_id: string;
  fingerprint: string;
  collection_name: string;
  storage_path: string;
  chunk_count: number;
  section_count: number;
  embedding_model: string;
  chunk_size: number;
  chunk_overlap: number;
  created_at: string;
  reused: boolean;
};

export type RetrievalCandidate = {
  chunk_id: string;
  text: string;
  metadata: Record<string, string | number | boolean | string[] | null>;
  dense_score: number;
  lexical_score: number;
  fused_score: number;
  rerank_score: number | null;
  citation_label: string;
};

export type AnswerResult = {
  question: string;
  answer: string;
  citations: string[];
  evidence: RetrievalCandidate[];
  supported: boolean;
  cache_status: {
    index_reused: boolean;
    answer_cache_hit: boolean;
  };
  model_name: string;
  note: string | null;
  citation_details: string[];
  retrieval_notes: string[];
  query_used: string;
  query_variants: string[];
};

export type HealthResponse = {
  status: string;
  app_name: string;
  retrieval_version: string;
  generation_version: string;
  openai_configured: boolean;
  supported_upload_types: string[];
};

export type DocumentBundleResponse = {
  document: DocumentRecord;
  index: IndexRecord | null;
};

export type CurrentWorkspaceResponse = {
  document: DocumentRecord | null;
  index: IndexRecord | null;
};

export type StarterQuestionsResponse = {
  document_id: string;
  document_style: string;
  questions: string[];
};

export type BenchmarkResponse = {
  report_name: string | null;
  report_path: string | null;
  report: Record<string, unknown> | null;
};

export type SampleDocument = {
  slug: string;
  file_name: string;
  title: string;
  category: string;
  description: string;
  size_bytes: number;
};
