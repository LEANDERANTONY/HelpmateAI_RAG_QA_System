"use client";

import { useMemo, useState } from "react";

import { askQuestion, buildIndex, getStarterQuestions, uploadDocument } from "@/lib/api";
import type { AuthUserSummary } from "@/lib/auth";
import type {
  AnswerResult,
  DocumentBundleResponse,
  DocumentRecord,
  IndexRecord,
} from "@/lib/api-types";

type AsyncState = "idle" | "loading" | "ready";
type InspectorTab = "evidence" | "debug";

type ParsedAnswerBlock =
  | { type: "definition-list"; items: Array<{ term: string; value: string }> }
  | { type: "paragraphs"; paragraphs: string[] };

type AppWorkspaceProps = {
  user: AuthUserSummary | null;
};

function stripInlineReferences(text: string) {
  return text.replace(/\s*references?\s*:\s*[\s\S]*$/i, "").trim();
}

function parseDefinitionStyleAnswer(text: string) {
  const normalized = text.trim();
  if (!normalized.startsWith("{") || !normalized.endsWith("}")) {
    return null;
  }

  const matches = [...normalized.matchAll(/'([^']+)'\s*:\s*'([^']*)'/g)];
  if (!matches.length) {
    return null;
  }

  const items = matches.map((match) => ({
    term: match[1].trim(),
    value: match[2].trim(),
  }));

  return items.length ? items : null;
}

function parseAnswerContent(text: string): ParsedAnswerBlock {
  const cleaned = stripInlineReferences(text);
  const definitionItems = parseDefinitionStyleAnswer(cleaned);

  if (definitionItems) {
    return {
      type: "definition-list",
      items: definitionItems,
    };
  }

  const paragraphs = cleaned
    .split(/\n\s*\n|\n(?=[-*•]\s)/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);

  return {
    type: "paragraphs",
    paragraphs: paragraphs.length ? paragraphs : [cleaned],
  };
}

export function AppWorkspace({ user }: AppWorkspaceProps) {
  const debugPanelEnabled =
    process.env.NEXT_PUBLIC_ENABLE_DEBUG_PANEL === "true";
  const isAuthenticated = Boolean(user);
  const [document, setDocument] = useState<DocumentRecord | null>(null);
  const [indexRecord, setIndexRecord] = useState<IndexRecord | null>(null);
  const [answer, setAnswer] = useState<AnswerResult | null>(null);
  const [starters, setStarters] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadState, setUploadState] = useState<AsyncState>("idle");
  const [indexState, setIndexState] = useState<AsyncState>("idle");
  const [answerState, setAnswerState] = useState<AsyncState>("idle");
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("evidence");
  const [lastAction, setLastAction] = useState("Waiting for a document.");
  const [error, setError] = useState<string | null>(null);

  const statusSummary = useMemo(() => {
    if (!isAuthenticated) {
      return "Sign in with Google from the left rail to unlock private uploads, indexing, and grounded answers.";
    }
    if (answerState === "loading") {
      return "Generating a grounded answer from the indexed document.";
    }
    if (indexState === "loading") {
      return "Building or reusing the local index for this document.";
    }
    if (uploadState === "loading") {
      return "Ingesting the selected document through the API boundary.";
    }
    return lastAction;
  }, [answerState, indexState, isAuthenticated, lastAction, uploadState]);

  const parsedAnswer = useMemo(
    () => (answer ? parseAnswerContent(answer.answer) : null),
    [answer],
  );

  function applyDocumentBundle(
    bundle: DocumentBundleResponse,
    nextAction: string,
  ) {
    setDocument(bundle.document);
    setIndexRecord(bundle.index);
    setAnswer(null);
    setQuestion("");
    setLastAction(nextAction);
    setInspectorTab("evidence");
  }

  async function refreshStarters(documentId: string) {
    try {
      const starterResponse = await getStarterQuestions(documentId);
      setStarters(starterResponse.questions);
    } catch {
      setStarters([]);
    }
  }

  async function handleUpload() {
    if (!selectedFile) {
      setError("Choose a PDF or DOCX before uploading.");
      return;
    }
    if (!isAuthenticated) {
      setError("Sign in with Google before uploading a document.");
      return;
    }
    setError(null);
    setUploadState("loading");
    setIndexState("idle");
    setLastAction(`Uploading ${selectedFile.name}...`);
    try {
      const uploadedBundle = await uploadDocument(selectedFile);
      applyDocumentBundle(
        uploadedBundle,
        `Uploaded ${uploadedBundle.document.file_name}.`,
      );
      setUploadState("ready");
      setSelectedFile(null);

      let finalBundle = uploadedBundle;
      if (!uploadedBundle.index) {
        setIndexState("loading");
        setLastAction(
          `Upload complete. Building the index for ${uploadedBundle.document.file_name}...`,
        );
        await new Promise((resolve) => window.setTimeout(resolve, 0));
        finalBundle = await buildIndex(uploadedBundle.document.document_id);
      }

      applyDocumentBundle(
        finalBundle,
        `Uploaded ${finalBundle.document.file_name} and prepared it for questions.`,
      );
      setIndexState(finalBundle.index ? "ready" : "idle");
      await refreshStarters(finalBundle.document.document_id);
    } catch (uploadError) {
      setUploadState("idle");
      setIndexState("idle");
      setError(
        uploadError instanceof Error
          ? uploadError.message
          : "Upload failed unexpectedly.",
      );
    }
  }

  async function handleAsk() {
    if (!document) {
      setError("Upload a document first.");
      return;
    }
    if (!isAuthenticated) {
      setError("Sign in with Google before generating answers.");
      return;
    }
    if (!indexRecord) {
      setError("This document is still being prepared. Try again in a moment.");
      return;
    }
    if (!question.trim()) {
      setError("Enter a question before generating an answer.");
      return;
    }
    setError(null);
    setAnswerState("loading");
    try {
      const response = await askQuestion(document.document_id, question);
      setAnswer(response.answer);
      setLastAction("Answer generated successfully.");
      setInspectorTab("evidence");
      setAnswerState("ready");
    } catch (answerError) {
      setAnswerState("idle");
      setError(
        answerError instanceof Error
          ? answerError.message
          : "Answer generation failed unexpectedly.",
      );
    }
  }

  return (
    <div className="grid gap-6">
      <section className="p-1">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="max-w-3xl">
            <p className="eyebrow">Workspace</p>
            <h1 className="section-heading text-[2.15rem] leading-[0.98] md:text-[3.05rem]">
              RAG augmented document QA
            </h1>
          </div>
        </div>

        <div className="mt-7 rounded-[1.6rem] border border-white/8 bg-white/[0.018] p-4 shadow-[0_12px_28px_rgba(0,0,0,0.14)] md:p-[1.125rem]">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="max-w-2xl">
                <p className="text-[0.68rem] uppercase tracking-[0.26em] text-blue-200/75">
                  Live state
                </p>
              <p className="mt-1.5 text-[0.96rem] font-medium leading-6 text-white md:text-[1rem]">
                {statusSummary}
              </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <span className="status-chip">
                  Document {document ? "loaded" : "pending"}
                </span>
              <span className="status-chip">
                Index {indexRecord ? "ready" : indexState === "loading" ? "building" : "pending"}
              </span>
              <span className="status-chip">
                Answer {answer ? "ready" : answerState === "loading" ? "running" : "idle"}
              </span>
            </div>
          </div>
        </div>

        <div className="mt-8 grid gap-6">
          <div className="surface-card surface-card-neutral">
            <p className="eyebrow">Step 1</p>
            <h2 className="text-xl font-semibold text-white">Upload your document</h2>
            <p className="mt-2 text-[0.96rem] leading-7 text-slate-300">
              Upload one PDF or DOCX file and Helpmate will prepare it for
              questioning in the workspace.
            </p>

            <div className="mt-5 rounded-[1.5rem] border border-dashed border-white/10 bg-black/20 p-5 shadow-[inset_0_1px_0_rgba(255,255,255,0.02)]">
              <span className="inline-flex rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 text-xs uppercase tracking-[0.26em] text-blue-200">
                One document at a time
              </span>
              <p className="mt-4 text-[1.08rem] font-medium text-white md:text-[1.14rem]">
                Bring in a new document from your machine.
              </p>
              <p className="mt-2 text-[0.95rem] leading-7 text-slate-400">
                The workspace supports one active document at a time so the
                answer, evidence, and indexing state stay clear.
              </p>
              <div className="mt-4 flex flex-wrap items-center gap-4">
                <label
                  className={`inline-flex items-center rounded-full bg-white px-4 py-2 text-sm font-medium text-slate-950 ${isAuthenticated ? "cursor-pointer" : "cursor-not-allowed opacity-60"}`}
                  htmlFor="document-upload"
                >
                  Choose File
                </label>
                <input
                  accept=".pdf,.docx"
                  className="sr-only"
                  disabled={!isAuthenticated}
                  id="document-upload"
                  onChange={(event) =>
                    setSelectedFile(event.target.files?.[0] ?? null)
                  }
                  type="file"
                />
                <p className="text-sm text-slate-300">
                  {selectedFile
                    ? selectedFile.name
                    : document && uploadState === "ready"
                      ? document.file_name
                      : "No file chosen"}
                </p>
              </div>
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                <button
                  className="primary-button w-auto px-5 py-3"
                  disabled={uploadState === "loading" || !isAuthenticated}
                  onClick={handleUpload}
                  type="button"
                >
                  {uploadState === "loading" ? "Uploading..." : "Upload document"}
                </button>
              </div>
              {document && uploadState === "ready" ? (
                <div className="mt-4 rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-3 text-sm text-blue-100 shadow-[inset_0_1px_0_rgba(255,255,255,0.02)]">
                  {indexRecord
                    ? `${document.file_name} is uploaded and ready for questions.`
                    : `${document.file_name} is uploaded successfully.`}
                </div>
              ) : null}
              {indexState === "loading" ? (
                <div className="mt-4 rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-3 text-sm text-blue-100">
                  {document
                    ? `Upload complete. Building the index for ${document.file_name}...`
                    : "Preparing the document workspace..."}
                </div>
              ) : null}
              {!isAuthenticated ? (
                <div className="mt-4 rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-3 text-sm text-slate-200">
                  Sign in first. This workspace will later enforce one active document per user and time-bound retention.
                </div>
              ) : null}
            </div>
          </div>

          <div className="surface-card surface-card-neutral">
            <p className="eyebrow">Step 2</p>
            <h2 className="text-xl font-semibold text-white">Index and ask</h2>
            <p className="mt-2 text-[0.96rem] leading-7 text-slate-300">
              Once a document is active, the workspace exposes status, starter
              questions, answer generation, and inspectable evidence.
            </p>

            <div className="mt-5 grid gap-3 lg:grid-cols-2">
              <div className="rounded-[1.5rem] border border-white/10 bg-black/25 p-5 shadow-[inset_0_1px_0_rgba(255,255,255,0.02)]">
                <span className="soft-panel-label">Document</span>
                <p className="mt-3 text-lg font-medium text-white">
                  {document?.file_name ?? "Nothing loaded yet"}
                </p>
                <p className="mt-2 text-sm text-white">
                  {document
                    ? `${document.file_type.toUpperCase()} - ${document.page_count ?? "?"} pages`
                    : "Upload a file first"}
                </p>
              </div>
              <div className="rounded-[1.5rem] border border-white/10 bg-black/25 p-5 shadow-[inset_0_1px_0_rgba(255,255,255,0.02)]">
                <span className="soft-panel-label">Index</span>
                <p className="mt-3 text-lg font-medium text-white">
                  {indexRecord ? "Ready" : "Pending"}
                </p>
                <p className="mt-2 text-sm text-white">
                  {indexRecord
                    ? `${indexRecord.chunk_count} chunks - ${indexRecord.section_count} sections`
                    : "Build or reuse an index for the active document"}
                </p>
              </div>
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-3">
              <span className="text-[0.95rem] text-slate-300">
                {indexState === "loading"
                  ? "The document is being prepared for retrieval and grounded QA."
                  : indexRecord
                    ? `Embedding model: ${indexRecord.embedding_model}`
                    : document
                      ? "The workspace will expose starter questions and answer generation once preparation completes."
                      : "This becomes available after a document upload."}
              </span>
            </div>

            {starters.length > 0 ? (
              <div className="mt-6">
                <p className="eyebrow">Starter questions</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {starters.map((starter) => (
                    <button
                      className="rounded-full border border-blue-300/16 bg-white/[0.03] px-4 py-2 text-left text-sm leading-5 text-slate-100 transition hover:border-blue-300/40 hover:bg-blue-300/10"
                      key={starter}
                      onClick={() => setQuestion(starter)}
                      type="button"
                    >
                      {starter}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}

            <div className="mt-6">
              <label className="text-[0.96rem] font-medium text-slate-300" htmlFor="question">
                Ask a grounded question
              </label>
              <textarea
                className="mt-3 min-h-40 w-full resize-y rounded-[1.5rem] border border-white/10 bg-black/25 px-4 py-4 text-base text-white outline-none transition placeholder:text-slate-500 focus:border-blue-300/50"
                disabled={!isAuthenticated}
                id="question"
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="What are the key exclusions, deadlines, findings, or future directions in this document?"
                value={question}
              />
              <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
                <p className="text-[0.95rem] text-slate-300">
                  {answerState === "loading"
                    ? "Running retrieval, routing, and answer generation."
                    : "Answers will come back with citations, evidence, and retrieval notes."}
                </p>
                <button
                  className="primary-button w-auto px-5 py-3"
                  disabled={answerState === "loading" || !isAuthenticated}
                  onClick={handleAsk}
                  type="button"
                >
                  {answerState === "loading" ? "Generating answer..." : "Generate answer"}
                </button>
              </div>
            </div>
          </div>
        </div>

        {error ? (
          <div className="mt-6 rounded-3xl border border-rose-400/25 bg-rose-400/10 px-4 py-3 text-sm text-rose-100">
            {error}
          </div>
        ) : null}
      </section>

      <aside className="grid gap-6">
        <section className="overflow-hidden">
          <div className="grid gap-6">
            <div className="surface-card surface-card-neutral">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="max-w-3xl">
                  <p className="eyebrow">Answer</p>
                  <h2 className="mt-2.5 font-[family:var(--font-space-grotesk)] text-3xl font-semibold tracking-[-0.04em] text-white">
                    {answer ? "Readable answer with source visibility" : "Answer panel ready when you are"}
                  </h2>
                </div>
                {answer ? (
                    <span className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 text-xs uppercase tracking-[0.24em] text-blue-100 shadow-[0_8px_20px_rgba(44,91,255,0.08)]">
                      {answer.supported ? "Supported" : "Unsupported"}
                    </span>
                ) : null}
              </div>

              {answer ? (
                <div className="mt-4 space-y-4">
                  <div className="flex flex-wrap items-center gap-2.5 text-sm text-slate-300">
                    <span className="workspace-meta-chip">
                      {answer.cache_status.answer_cache_hit ? "Cache hit" : "Fresh answer"}
                    </span>
                    <span className="workspace-meta-chip">
                      {answer.model_name || "gpt-4o-mini"}
                    </span>
                    <span className="workspace-meta-chip">
                      {answer.citations.length} citations
                    </span>
                  </div>

                  <div className="rounded-[1.75rem] border border-white/10 bg-black/25 p-5 shadow-[inset_0_1px_0_rgba(255,255,255,0.02)] md:p-[1.375rem]">
                    {parsedAnswer?.type === "definition-list" ? (
                      <div className="grid gap-3 md:grid-cols-2">
                        {parsedAnswer.items.map((item) => (
                          <article
                            className="rounded-[1.25rem] border border-blue-300/10 bg-black/20 p-4"
                            key={item.term}
                          >
                            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-blue-100/80">
                              {item.term}
                            </p>
                            <p className="mt-3 text-[0.98rem] leading-7 text-white">
                              {item.value}
                            </p>
                          </article>
                        ))}
                      </div>
                    ) : (
                      <div className="space-y-3">
                        {parsedAnswer?.paragraphs.map((paragraph, index) => (
                          <p className="text-[1.08rem] leading-8 text-white md:text-[1.12rem] md:leading-9" key={`${index}-${paragraph.slice(0, 24)}`}>
                            {paragraph}
                          </p>
                        ))}
                      </div>
                    )}
                  </div>

                  {answer.citation_details.length > 0 ? (
                    <div>
                      <h3 className="text-sm font-semibold uppercase tracking-[0.22em] text-blue-100/85">
                        Citation trail
                      </h3>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {answer.citation_details.map((citation) => (
                          <span
                            className="rounded-full border border-blue-300/12 bg-white/[0.03] px-3.5 py-2 text-sm text-slate-100"
                            key={citation}
                          >
                            {citation}
                          </span>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {answer.note ? (
                    <div className="rounded-[1.25rem] border border-white/10 bg-black/25 p-4 text-[0.98rem] leading-7 text-white">
                      {answer.note}
                    </div>
                  ) : null}
                </div>
              ) : (
                <div className="mt-5 rounded-[1.75rem] border border-white/10 bg-black/25 p-5 text-[0.96rem] leading-7 text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.02)]">
                  The answer panel fills in after a document is indexed and a question is asked. Once it runs,
                  this area will show the answer, support status, and its citation trail.
                </div>
              )}
            </div>

            <div className="surface-card surface-card-neutral">
              {debugPanelEnabled ? (
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    className={
                      inspectorTab === "evidence"
                        ? "inline-flex items-center rounded-full border border-white/10 bg-white/[0.03] px-4 py-2 text-sm font-medium text-white"
                        : "inline-flex items-center rounded-full bg-white px-4 py-2 text-sm font-medium text-slate-950"
                    }
                    onClick={() => setInspectorTab("evidence")}
                    type="button"
                  >
                    Evidence
                  </button>
                  <button
                    className={
                      inspectorTab === "debug"
                        ? "inline-flex items-center rounded-full border border-white/10 bg-white/[0.03] px-4 py-2 text-sm font-medium text-white"
                        : "inline-flex items-center rounded-full bg-white px-4 py-2 text-sm font-medium text-slate-950"
                    }
                    onClick={() => setInspectorTab("debug")}
                    type="button"
                  >
                    Debug
                  </button>
                </div>
              ) : (
                <div>
                  <span className="soft-panel-label">Evidence</span>
                </div>
              )}

              <div className="mt-5">
                {(!debugPanelEnabled || inspectorTab === "evidence") ? (
                  <div className="space-y-3">
                    {answer?.evidence?.length ? (
                      answer.evidence.slice(0, 4).map((candidate) => (
                        <article className="rounded-[1.5rem] border border-white/10 bg-black/25 p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.02)]" key={candidate.chunk_id}>
                          <div className="flex items-center justify-between gap-3">
                            <span className="soft-panel-label">
                              {candidate.citation_label || "Evidence"}
                            </span>
                            <span className="text-xs text-slate-500">
                              {candidate.metadata.section_kind?.toString() ??
                                candidate.metadata.content_type?.toString() ??
                                "Chunk"}
                            </span>
                          </div>
                          <p className="mt-3 overflow-hidden text-[1rem] leading-7 text-white md:text-[1.02rem] [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:7]">
                            {candidate.text}
                          </p>
                        </article>
                      ))
                    ) : (
                      <div className="rounded-[1.5rem] border border-white/10 bg-black/25 p-4 text-sm leading-6 text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.02)]">
                        Retrieved evidence cards will appear here once the backend answers a question.
                      </div>
                    )}
                  </div>
                ) : null}

                {debugPanelEnabled && inspectorTab === "debug" ? (
                  <div className="space-y-4">
                    <div className="rounded-[1.5rem] border border-white/10 bg-black/25 p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.02)]">
                      <span className="soft-panel-label">Active query</span>
                      <p className="mt-3 text-sm leading-6 text-white">
                        {answer?.query_used ?? "Ask a question to populate query details."}
                      </p>
                    </div>
                    <div className="rounded-[1.5rem] border border-white/10 bg-black/25 p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.02)]">
                      <span className="soft-panel-label">Variants</span>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {(answer?.query_variants ?? []).length ? (
                          answer?.query_variants.map((variant) => (
                            <span
                              className="rounded-full border border-blue-300/12 bg-white/[0.03] px-3 py-1 text-xs text-slate-100"
                              key={variant}
                            >
                              {variant}
                            </span>
                          ))
                        ) : (
                          <span className="text-sm text-white">No query variants yet.</span>
                        )}
                      </div>
                    </div>
                    <div className="rounded-[1.5rem] border border-white/10 bg-black/25 p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.02)]">
                      <span className="soft-panel-label">Retrieval notes</span>
                      {(answer?.retrieval_notes ?? []).length ? (
                        <ul className="mt-3 space-y-2 text-sm text-white">
                          {answer?.retrieval_notes.map((note) => (
                            <li key={note}>{note}</li>
                          ))}
                        </ul>
                      ) : (
                        <p className="mt-3 text-sm text-white">
                          Retrieval notes will appear after the first answer.
                        </p>
                      )}
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        </section>
      </aside>
    </div>
  );
}
