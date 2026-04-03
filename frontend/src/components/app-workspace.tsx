"use client";

import { startTransition, useEffect, useMemo, useState } from "react";

import {
  askQuestion,
  buildIndex,
  getHealth,
  getStarterQuestions,
  uploadDocument,
} from "@/lib/api";
import type {
  AnswerResult,
  DocumentBundleResponse,
  DocumentRecord,
  HealthResponse,
  IndexRecord,
} from "@/lib/api-types";

type AsyncState = "idle" | "loading" | "ready";
type InspectorTab = "evidence" | "debug";

type ParsedAnswerBlock =
  | { type: "definition-list"; items: Array<{ term: string; value: string }> }
  | { type: "paragraphs"; paragraphs: string[] };

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

export function AppWorkspace() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
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

  useEffect(() => {
    let cancelled = false;

    async function bootstrap() {
      try {
        const healthResponse = await getHealth();
        if (cancelled) {
          return;
        }
        startTransition(() => {
          setHealth(healthResponse);
        });
      } catch (bootstrapError) {
        if (!cancelled) {
          setError(
            bootstrapError instanceof Error
              ? bootstrapError.message
              : "Unable to reach the backend.",
          );
        }
      }
    }

    bootstrap();
    return () => {
      cancelled = true;
    };
  }, []);

  const healthBadge = useMemo(() => {
    if (!health) {
      return "Connecting";
    }
    return health.openai_configured ? "API Ready" : "Backend Ready";
  }, [health]);

  const statusSummary = useMemo(() => {
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
  }, [answerState, indexState, lastAction, uploadState]);

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
    setError(null);
    setUploadState("loading");
    setIndexState("idle");
    try {
      const uploadedBundle = await uploadDocument(selectedFile);
      applyDocumentBundle(
        uploadedBundle,
        `Uploaded ${uploadedBundle.document.file_name}. Preparing the workspace now.`,
      );
      setUploadState("ready");
      setSelectedFile(null);

      let finalBundle = uploadedBundle;
      if (!uploadedBundle.index) {
        setIndexState("loading");
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

  async function handleBuildIndex() {
    if (!document) {
      setError("Upload a document first.");
      return;
    }
    setError(null);
    setIndexState("loading");
    try {
      const bundle = await buildIndex(document.document_id);
      applyDocumentBundle(
        bundle,
        `Index ready for ${bundle.document.file_name}.`,
      );
      await refreshStarters(bundle.document.document_id);
      setIndexState("ready");
    } catch (indexError) {
      setIndexState("idle");
      setError(
        indexError instanceof Error
          ? indexError.message
          : "Index build failed unexpectedly.",
      );
    }
  }

  async function handleAsk() {
    if (!document) {
      setError("Upload and index a document first.");
      return;
    }
    if (!indexRecord) {
      setError("Build or reuse the index before asking a question.");
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
      <section className="glass-panel p-6 md:p-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="max-w-3xl">
            <p className="eyebrow">Workspace</p>
            <h1 className="section-heading text-3xl md:text-4xl">
              Premium QA surface over the existing RAG core
            </h1>
          </div>
          <div className="rounded-full border border-white/12 bg-white/6 px-4 py-2 text-sm text-slate-200">
            {healthBadge}
          </div>
        </div>

        <div className="mt-6 rounded-[1.75rem] border border-cyan-300/15 bg-cyan-300/8 p-4 md:p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="max-w-2xl">
              <p className="text-xs uppercase tracking-[0.26em] text-cyan-200/80">
                Live state
              </p>
              <p className="mt-2 text-base font-medium text-white">
                {statusSummary}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <span className="status-chip">
                API {health ? "online" : "connecting"}
              </span>
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

        <div className="mt-8 grid gap-5">
          <div className="surface-card surface-card-teal">
            <p className="eyebrow">Step 1</p>
            <h2 className="text-xl font-semibold text-white">Upload your document</h2>
            <p className="mt-2 text-sm leading-6 text-slate-300">
              Upload one PDF or DOCX file and Helpmate will prepare it for
              questioning in the workspace.
            </p>

            <div className="mt-5 rounded-[1.5rem] border border-dashed border-cyan-400/30 bg-cyan-400/6 p-5">
              <span className="inline-flex rounded-full border border-cyan-300/20 bg-cyan-300/10 px-3 py-1 text-xs uppercase tracking-[0.26em] text-cyan-200">
                One document at a time
              </span>
              <p className="mt-4 text-lg font-medium text-white">
                Bring in a new document from your machine.
              </p>
              <p className="mt-2 text-sm leading-6 text-slate-400">
                The workspace supports one active document at a time so the
                answer, evidence, and indexing state stay clear.
              </p>
              <input
                accept=".pdf,.docx"
                className="mt-5 block text-sm text-slate-300 file:mr-4 file:rounded-full file:border-0 file:bg-white file:px-4 file:py-2 file:text-sm file:font-medium file:text-slate-950"
                onChange={(event) =>
                  setSelectedFile(event.target.files?.[0] ?? null)
                }
                type="file"
              />
              <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
                <p className="max-w-xs text-sm text-slate-300">
                  {selectedFile
                    ? selectedFile.name
                    : document && uploadState === "ready"
                      ? `${document.file_name} is loaded`
                      : "No local file selected yet."}
                </p>
                <button
                  className="primary-button w-auto px-5 py-3"
                  disabled={uploadState === "loading"}
                  onClick={handleUpload}
                  type="button"
                >
                  {uploadState === "loading" ? "Uploading..." : "Upload document"}
                </button>
              </div>
              {document && uploadState === "ready" ? (
                <div className="mt-4 rounded-2xl border border-emerald-300/20 bg-emerald-300/10 px-4 py-3 text-sm text-emerald-100">
                  {indexRecord
                    ? `${document.file_name} is uploaded and ready for questions.`
                    : `${document.file_name} is uploaded successfully.`}
                </div>
              ) : null}
            </div>
          </div>

          <div className="surface-card">
            <p className="eyebrow">Step 2</p>
            <h2 className="text-xl font-semibold text-white">Index and ask</h2>
            <p className="mt-2 text-sm leading-6 text-slate-300">
              Once a document is active, the workspace exposes status, starter
              questions, answer generation, and inspectable evidence.
            </p>

            <div className="mt-5 grid gap-3 lg:grid-cols-2">
              <div className="soft-panel soft-panel-teal">
                <span className="soft-panel-label">Document</span>
                <p className="mt-3 text-lg font-medium text-white">
                  {document?.file_name ?? "Nothing loaded yet"}
                </p>
                <p className="mt-2 text-sm text-slate-400">
                  {document
                    ? `${document.file_type.toUpperCase()} - ${document.page_count ?? "?"} pages`
                    : "Upload a file first"}
                </p>
              </div>
              <div className="soft-panel soft-panel-teal">
                <span className="soft-panel-label">Index</span>
                <p className="mt-3 text-lg font-medium text-white">
                  {indexRecord ? "Ready" : "Pending"}
                </p>
                <p className="mt-2 text-sm text-slate-400">
                  {indexRecord
                    ? `${indexRecord.chunk_count} chunks - ${indexRecord.section_count} sections`
                    : "Build or reuse an index for the active document"}
                </p>
              </div>
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-3">
              <button
                className="secondary-button w-auto px-5 py-3"
                disabled={!document || indexState === "loading"}
                onClick={handleBuildIndex}
                type="button"
              >
                {indexState === "loading" ? "Building index..." : "Build or reuse index"}
              </button>
              <span className="text-sm text-slate-400">
                {indexRecord
                  ? `Embedding model: ${indexRecord.embedding_model}`
                  : "No index available yet."}
              </span>
            </div>

            {starters.length > 0 ? (
              <div className="mt-6">
                <p className="eyebrow">Starter questions</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {starters.map((starter) => (
                    <button
                      className="rounded-full border border-white/10 bg-white/6 px-4 py-2 text-left text-sm leading-5 text-slate-200 transition hover:border-cyan-300/40 hover:bg-cyan-300/10"
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
              <label className="text-sm font-medium text-slate-300" htmlFor="question">
                Ask a grounded question
              </label>
              <textarea
                className="mt-3 min-h-40 w-full resize-y rounded-[1.5rem] border border-white/10 bg-black/25 px-4 py-4 text-base text-white outline-none transition placeholder:text-slate-500 focus:border-cyan-300/50"
                id="question"
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="What are the key exclusions, deadlines, findings, or future directions in this document?"
                value={question}
              />
              <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
                <p className="text-sm text-slate-400">
                  {answerState === "loading"
                    ? "Running retrieval, routing, and answer generation."
                    : "Answers will come back with citations, evidence, and retrieval notes."}
                </p>
                <button
                  className="primary-button w-auto px-5 py-3"
                  disabled={answerState === "loading"}
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
        <section className="glass-panel overflow-hidden p-6 md:p-7">
          <div className="grid gap-6">
            <div className="workspace-answer-panel workspace-answer-panel-teal">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="eyebrow">Answer</p>
                  <h2 className="mt-3 font-[family:var(--font-space-grotesk)] text-3xl font-semibold tracking-[-0.04em] text-white">
                    {answer ? "Readable answer with source visibility" : "Answer panel ready when you are"}
                  </h2>
                </div>
                {answer ? (
                  <span className="rounded-full border border-cyan-300/25 bg-cyan-300/10 px-3 py-1 text-xs uppercase tracking-[0.24em] text-cyan-100">
                    {answer.supported ? "Supported" : "Unsupported"}
                  </span>
                ) : null}
              </div>

              {answer ? (
                <div className="mt-6 space-y-5">
                  <div className="flex flex-wrap items-center gap-3 text-sm text-slate-400">
                    <span className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1.5">
                      {answer.cache_status.answer_cache_hit ? "Cache hit" : "Fresh answer"}
                    </span>
                    <span className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1.5">
                      {answer.model_name || "gpt-4o-mini"}
                    </span>
                    <span className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1.5">
                      {answer.citations.length} citations
                    </span>
                  </div>

                  <div className="rounded-[1.75rem] border border-white/10 bg-white/[0.025] p-5 md:p-6">
                    {parsedAnswer?.type === "definition-list" ? (
                      <div className="grid gap-3 md:grid-cols-2">
                        {parsedAnswer.items.map((item) => (
                          <article
                            className="rounded-[1.25rem] border border-white/8 bg-black/20 p-4"
                            key={item.term}
                          >
                            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-cyan-100/80">
                              {item.term}
                            </p>
                            <p className="mt-3 text-[0.98rem] leading-7 text-slate-100">
                              {item.value}
                            </p>
                          </article>
                        ))}
                      </div>
                    ) : (
                      <div className="space-y-4">
                        {parsedAnswer?.paragraphs.map((paragraph, index) => (
                          <p className="text-[1.02rem] leading-8 text-slate-100" key={`${index}-${paragraph.slice(0, 24)}`}>
                            {paragraph}
                          </p>
                        ))}
                      </div>
                    )}
                  </div>

                  {answer.citation_details.length > 0 ? (
                    <div>
                      <h3 className="text-sm font-semibold uppercase tracking-[0.22em] text-cyan-100/85">
                        Citation trail
                      </h3>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {answer.citation_details.map((citation) => (
                          <span
                            className="rounded-full border border-white/10 bg-white/6 px-3 py-1.5 text-xs text-slate-200"
                            key={citation}
                          >
                            {citation}
                          </span>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {answer.note ? (
                    <div className="rounded-[1.25rem] border border-white/8 bg-black/20 p-4 text-sm leading-6 text-slate-300">
                      {answer.note}
                    </div>
                  ) : null}
                </div>
              ) : (
                <div className="mt-6 rounded-[1.75rem] border border-dashed border-white/10 bg-white/[0.025] p-6 text-sm leading-7 text-slate-400">
                  The answer panel fills in after a document is indexed and a question is asked. Once it runs,
                  this area will show the answer, support status, and its citation trail.
                </div>
              )}
            </div>

            <div className="workspace-inspector-shell workspace-inspector-shell-teal">
              <div className="flex flex-wrap items-center gap-2">
                <button
                  className={`inspector-tab ${inspectorTab === "evidence" ? "inspector-tab-active" : ""}`}
                  onClick={() => setInspectorTab("evidence")}
                  type="button"
                >
                  Evidence
                </button>
                <button
                  className={`inspector-tab ${inspectorTab === "debug" ? "inspector-tab-active" : ""}`}
                  onClick={() => setInspectorTab("debug")}
                  type="button"
                >
                  Debug
                </button>
              </div>

              <div className="mt-5">
                {inspectorTab === "evidence" ? (
                  <div className="space-y-3">
                    {answer?.evidence?.length ? (
                      answer.evidence.slice(0, 4).map((candidate) => (
                        <article className="soft-panel soft-panel-teal" key={candidate.chunk_id}>
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
                          <p className="mt-3 overflow-hidden text-sm leading-6 text-slate-200 [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:6]">
                            {candidate.text}
                          </p>
                        </article>
                      ))
                    ) : (
                      <div className="rounded-[1.5rem] border border-dashed border-white/10 bg-white/[0.03] p-4 text-sm leading-6 text-slate-400">
                        Retrieved evidence cards will appear here once the backend answers a question.
                      </div>
                    )}
                  </div>
                ) : null}

                {inspectorTab === "debug" ? (
                  <div className="space-y-4">
                    <div className="soft-panel">
                      <span className="soft-panel-label">Active query</span>
                      <p className="mt-3 text-sm leading-6 text-slate-200">
                        {answer?.query_used ?? "Ask a question to populate query details."}
                      </p>
                    </div>
                    <div className="soft-panel">
                      <span className="soft-panel-label">Variants</span>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {(answer?.query_variants ?? []).length ? (
                          answer?.query_variants.map((variant) => (
                            <span
                              className="rounded-full border border-white/10 bg-white/6 px-3 py-1 text-xs text-slate-200"
                              key={variant}
                            >
                              {variant}
                            </span>
                          ))
                        ) : (
                          <span className="text-sm text-slate-400">No query variants yet.</span>
                        )}
                      </div>
                    </div>
                    <div className="soft-panel">
                      <span className="soft-panel-label">Retrieval notes</span>
                      {(answer?.retrieval_notes ?? []).length ? (
                        <ul className="mt-3 space-y-2 text-sm text-slate-300">
                          {answer?.retrieval_notes.map((note) => (
                            <li key={note}>{note}</li>
                          ))}
                        </ul>
                      ) : (
                        <p className="mt-3 text-sm text-slate-400">
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
