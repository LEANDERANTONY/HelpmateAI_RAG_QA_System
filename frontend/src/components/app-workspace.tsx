"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  ChangeEvent,
  KeyboardEvent as ReactKeyboardEvent,
  MouseEvent as ReactMouseEvent,
  ReactNode,
} from "react";

import { AuthSidebar } from "@/components/auth-sidebar";
import { ErrorState } from "@/components/error-state";
import { MobileSourceSheet } from "@/components/viewer/mobile-source-sheet";
import { SourcePane } from "@/components/viewer/source-pane";
import { askQuestion, buildIndex, getCurrentWorkspace, getStarterQuestions, uploadDocument } from "@/lib/api";
import { ApiError } from "@/lib/api-errors";
import type { AuthUserSummary } from "@/lib/auth";
import { notifyApiError, notifyError, notifySuccess } from "@/lib/toast";
import {
  splitCitationSegments,
  stripReferencesBlock,
  uniqueCitationTargets,
} from "@/lib/citations";
import {
  useMobileSnap,
  useReadModeActions,
  useReadModeStatus,
  useReadModeStore,
  type ReadModeChunk,
} from "@/lib/read-mode-state";
import { useMediaQuery } from "@/lib/use-media-query";
import type {
  AnswerResult,
  DocumentBundleResponse,
  DocumentRecord,
  IndexRecord,
  RetrievalCandidate,
} from "@/lib/api-types";

type AsyncState = "idle" | "loading" | "ready" | "error";
type SupportStatus = AnswerResult["support_status"];

type AppWorkspaceProps = {
  user: AuthUserSummary | null;
};

type QATurn = {
  id: string;
  question: string;
  answer: AnswerResult;
  askedAt: Date;
};

type DefinitionItem = {
  term: string;
  value: string;
};

type IconProps = {
  size?: number;
};

const FLASH_MS = 1400;
const STREAM_MAX_MS = 3400;
const STREAM_MIN_MS = 1400;
const STREAM_BASE_MS = 600;
const STREAM_PER_CHAR_MS = 4;

function streamingDuration(text: string) {
  return Math.min(
    STREAM_MAX_MS,
    Math.max(STREAM_MIN_MS, STREAM_BASE_MS + text.length * STREAM_PER_CHAR_MS),
  );
}

function useTypingProgress(text: string, durationMs: number, active: boolean) {
  const [chars, setChars] = useState(active ? 0 : text.length);
  useEffect(() => {
    let raf = 0;
    if (!active) {
      raf = requestAnimationFrame(() => setChars(text.length));
      return () => cancelAnimationFrame(raf);
    }
    const start = performance.now();
    const tick = () => {
      const elapsed = performance.now() - start;
      const progress = Math.min(1, elapsed / Math.max(1, durationMs));
      setChars(Math.floor(progress * text.length));
      if (progress < 1) {
        raf = requestAnimationFrame(tick);
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [active, text, durationMs]);
  return chars;
}

const icons = {
  Chevron({ size = 14 }: IconProps) {
    return (
      <svg aria-hidden="true" height={size} viewBox="0 0 24 24" width={size}>
        <path d="m6 9 6 6 6-6" />
      </svg>
    );
  },
  Doc({ size = 14 }: IconProps) {
    return (
      <svg aria-hidden="true" height={size} viewBox="0 0 24 24" width={size}>
        <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
        <path d="M14 3v5h5" />
      </svg>
    );
  },
  More({ size = 14 }: IconProps) {
    return (
      <svg aria-hidden="true" height={size} viewBox="0 0 24 24" width={size}>
        <circle cx="6" cy="12" r="1.8" />
        <circle cx="12" cy="12" r="1.8" />
        <circle cx="18" cy="12" r="1.8" />
      </svg>
    );
  },
  Refresh({ size = 14 }: IconProps) {
    return (
      <svg aria-hidden="true" height={size} viewBox="0 0 24 24" width={size}>
        <path d="M3 12a9 9 0 0 1 15.5-6.3L21 8" />
        <path d="M21 3v5h-5" />
        <path d="M21 12a9 9 0 0 1-15.5 6.3L3 16" />
        <path d="M3 21v-5h5" />
      </svg>
    );
  },
  Search({ size = 14 }: IconProps) {
    return (
      <svg aria-hidden="true" height={size} viewBox="0 0 24 24" width={size}>
        <circle cx="11" cy="11" r="7" />
        <path d="m20 20-3.5-3.5" />
      </svg>
    );
  },
  Send({ size = 14 }: IconProps) {
    return (
      <svg aria-hidden="true" height={size} viewBox="0 0 24 24" width={size}>
        <path d="M5 12h14" />
        <path d="m13 6 6 6-6 6" />
      </svg>
    );
  },
  Swap({ size = 14 }: IconProps) {
    return (
      <svg aria-hidden="true" height={size} viewBox="0 0 24 24" width={size}>
        <path d="M7 7h13l-3-3" />
        <path d="M17 17H4l3 3" />
      </svg>
    );
  },
  Upload({ size = 16 }: IconProps) {
    return (
      <svg aria-hidden="true" height={size} viewBox="0 0 24 24" width={size}>
        <path d="M12 16V4" />
        <path d="m7 9 5-5 5 5" />
        <path d="M5 20h14" />
      </svg>
    );
  },
};

function makeTurnId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `qa-${Date.now()}`;
}

function metadataText(candidate: RetrievalCandidate, key: string) {
  const value = candidate.metadata[key];
  if (Array.isArray(value)) {
    return value.join(", ");
  }
  if (value === null || value === undefined || value === "") {
    return "";
  }
  return String(value);
}

function metadataHighlightTerms(candidate: RetrievalCandidate): string[] {
  const value = candidate.metadata.highlight_terms;
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter(
    (term): term is string => typeof term === "string" && term.length > 0,
  );
}

function escapeRegex(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function renderHighlightedText(text: string, terms: string[]): ReactNode[] {
  if (!terms.length) {
    return [text];
  }
  // Sort longer terms first so plurals like "obligations" beat the shorter
  // "obligation" stem to a match — otherwise the singular wins and the
  // trailing "s" renders unmarked.
  const sorted = [...terms].sort((a, b) => b.length - a.length);
  const pattern = new RegExp(`\\b(${sorted.map(escapeRegex).join("|")})\\b`, "gi");
  const segments: ReactNode[] = [];
  let lastIndex = 0;
  let key = 0;
  for (const match of text.matchAll(pattern)) {
    const start = match.index ?? 0;
    if (start > lastIndex) {
      segments.push(text.slice(lastIndex, start));
    }
    segments.push(<mark key={`mk-${key++}-${start}`}>{match[0]}</mark>);
    lastIndex = start + match[0].length;
  }
  if (lastIndex < text.length) {
    segments.push(text.slice(lastIndex));
  }
  return segments.length ? segments : [text];
}

function parseDefinitionStyleAnswer(text: string): DefinitionItem[] | null {
  const normalized = stripReferencesBlock(text).trim();
  if (!normalized.startsWith("{") || !normalized.endsWith("}")) {
    return null;
  }

  const matches = [...normalized.matchAll(/'([^']+)'\s*:\s*'([^']*)'/g)];
  if (!matches.length) {
    return null;
  }

  return matches.map((match) => ({
    term: match[1].trim(),
    value: match[2].trim(),
  }));
}

function formatNumber(value: number | null | undefined) {
  if (typeof value !== "number") {
    return "-";
  }
  return new Intl.NumberFormat("en", { maximumFractionDigits: 0 }).format(value);
}

function accountInitial(user: AuthUserSummary | null) {
  const seed = user?.displayName || user?.email || "H";
  return seed.trim().charAt(0).toUpperCase();
}

function accountLabel(user: AuthUserSummary | null) {
  return user?.email || user?.displayName || "Sign in";
}

function supportCopy(status: SupportStatus | null | undefined) {
  if (status === "partial") {
    return "PARTIAL";
  }
  if (status === "unsupported") {
    return "ABSTAINED";
  }
  return "SUPPORTED";
}

function relativeTime(value: Date) {
  const elapsedMs = Date.now() - value.getTime();
  const minutes = Math.floor(elapsedMs / 60000);
  if (minutes < 1) {
    return "just now";
  }
  if (minutes === 1) {
    return "1 min ago";
  }
  return `${minutes} min ago`;
}

function docStatus(
  indexState: AsyncState,
  indexRecord: IndexRecord | null,
  lastAnswer: AnswerResult | null,
) {
  if (indexState === "loading") {
    return { className: "pulse", label: "Building index" };
  }
  if (indexState === "error") {
    return { className: "danger", label: "Index failed" };
  }
  if (!indexRecord) {
    return { className: "muted", label: "Index pending" };
  }
  if (!lastAnswer) {
    return { className: "", label: "Ready for questions" };
  }
  if (lastAnswer.support_status === "partial") {
    return { className: "warn", label: "Last answer partial" };
  }
  if (lastAnswer.support_status === "unsupported") {
    return { className: "muted", label: "Last question abstained" };
  }
  return { className: "", label: "Last answer supported" };
}

function candidateLabel(candidate: RetrievalCandidate, index: number) {
  const section = metadataText(candidate, "section_heading") || metadataText(candidate, "section_id");
  const page = metadataText(candidate, "page_label");
  if (section && page) {
    return `${section} - ${page}`;
  }
  if (candidate.citation_label) {
    return candidate.citation_label;
  }
  if (page) {
    return page;
  }
  return `Source ${index + 1}`;
}

function candidateStrength(candidate: RetrievalCandidate) {
  // The current API has answer-level support, not per-chunk strength. Render
  // this visual only when an additive backend field is actually present.
  const value = metadataText(candidate, "evidence_strength").toLowerCase();
  if (value === "strong" || value === "weak" || value === "unsupported") {
    return value;
  }
  return null;
}

function candidateKind(candidate: RetrievalCandidate) {
  return (
    metadataText(candidate, "semantic_chunk_role") ||
    metadataText(candidate, "content_type") ||
    metadataText(candidate, "section_kind") ||
    "chunk"
  );
}

// Build the Read Mode store's chunk payload from a RetrievalCandidate.
// Used by both the "Open in source" entry path and the in-Read-Mode
// jump paths (auto-jump on new answer, citation pill click).
function buildReadModeChunk(
  candidate: RetrievalCandidate,
  fileName: string,
): ReadModeChunk {
  return {
    chunkId: candidate.chunk_id,
    pageLabel: metadataText(candidate, "page_label"),
    chunkText: candidate.text,
    documentId: metadataText(candidate, "document_id"),
    fileName,
  };
}

// Read Mode banner shown above the chat input when the most recent
// answer abstained. Self-contained: returns null outside Read Mode or
// when the latest answer has evidence, so callers can render this
// unconditionally without their own branching.
//
// We don't auto-clear this banner on a timer — it sits until the user
// asks another question (the next runAsk replaces `lastAnswer`). That
// matches the spec's "Source not yanked" intent: the abstention signal
// should be visible as long as the unsupported answer is the working
// context.
function ReadModeAbstentionBanner({ answer }: { answer: AnswerResult | null }) {
  const mode = useReadModeStatus();
  if (mode !== "read" || !answer) {
    return null;
  }
  // Treat both `support_status === 'unsupported'` and the empty-evidence
  // edge case as "abstained" — the latter shouldn't happen for an
  // unsupported answer from the backend, but the guard makes the banner
  // resilient if it ever does.
  const abstained = answer.support_status === "unsupported" || answer.evidence.length === 0;
  if (!abstained) {
    return null;
  }
  return (
    <div className="h-abstention-banner" role="status">
      This answer was abstained — no evidence to show. Source stays put.
    </div>
  );
}

function Hairline() {
  return <div className="h-hairline" />;
}

function Stat({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="h-stat">
      <span>{label}</span>
      <strong className={mono ? "mono" : ""}>{value}</strong>
    </div>
  );
}

function SupportPip({
  className = "",
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return <span className={`h-pip ${className}`.trim()}>{children}</span>;
}

function AccountTopbar({
  user,
  open,
  onToggle,
  onClose,
}: {
  user: AuthUserSummary | null;
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocPointer(event: globalThis.MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(event.target as Node)) {
        onClose();
      }
    }
    function onKey(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    }
    const doc = window.document;
    doc.addEventListener("mousedown", onDocPointer);
    doc.addEventListener("keydown", onKey, true);
    return () => {
      doc.removeEventListener("mousedown", onDocPointer);
      doc.removeEventListener("keydown", onKey, true);
    };
  }, [onClose, open]);

  return (
    <div className="h-account-wrap" ref={wrapRef}>
      <button
        aria-expanded={open}
        className="h-account"
        onClick={onToggle}
        type="button"
      >
        {user ? (
          <span className="h-avatar">{accountInitial(user)}</span>
        ) : (
          <img
            alt=""
            aria-hidden="true"
            className="h-avatar h-avatar-logo"
            height={26}
            src="/brand/helpmate-icon.svg"
            width={26}
          />
        )}
        <span className="h-account-email">{accountLabel(user)}</span>
        <icons.Chevron size={12} />
      </button>
      {open ? (
        <div className="h-account-popover">
          <AuthSidebar user={user} />
        </div>
      ) : null}
    </div>
  );
}

function Topbar({
  user,
  accountOpen,
  onAccountToggle,
  onAccountClose,
  onPaletteOpen,
}: {
  user: AuthUserSummary | null;
  accountOpen: boolean;
  onAccountToggle: () => void;
  onAccountClose: () => void;
  onPaletteOpen: () => void;
}) {
  return (
    <header className="h-topbar">
      <div className="h-topbar-inner">
        <div className="h-brand">
          <img
            alt=""
            aria-hidden="true"
            className="h-brand-mark"
            height={22}
            src="/brand/helpmate-icon.svg"
            width={22}
          />
          <div className="h-wordmark">Helpmate AI</div>
        </div>
        <button
          aria-label="Open search palette"
          className="h-palette"
          onClick={onPaletteOpen}
          type="button"
        >
          <span>
            <icons.Search size={13} />
            <span>Search documents and questions</span>
          </span>
          <kbd>⌘K</kbd>
        </button>
        <div className="h-spacer" />
        <AccountTopbar
          onClose={onAccountClose}
          onToggle={onAccountToggle}
          open={accountOpen}
          user={user}
        />
      </div>
    </header>
  );
}

function UploadDropzone({
  disabled,
  selectedFile,
  onFileChange,
  onUpload,
  uploadState,
}: {
  disabled: boolean;
  selectedFile: File | null;
  onFileChange: (event: ChangeEvent<HTMLInputElement>) => void;
  onUpload: () => void;
  uploadState: AsyncState;
}) {
  return (
    <div className="h-drop">
      <input
        accept=".pdf,.docx"
        className="h-sr-only"
        disabled={disabled}
        id="document-upload"
        onChange={onFileChange}
        type="file"
      />
      <label className={`h-drop-zone ${disabled ? "disabled" : ""}`} htmlFor="document-upload">
        <span className="h-drop-icon">
          <icons.Upload />
        </span>
        <span className="label">{selectedFile ? selectedFile.name : "Drop file here"}</span>
        <span className="sub">{selectedFile ? "Ready to upload" : "or choose file"}</span>
        <span className="hint">PDF - DOCX</span>
      </label>
      <button
        className="h-btn h-btn-primary"
        disabled={disabled || !selectedFile || uploadState === "loading"}
        onClick={onUpload}
        type="button"
      >
        {uploadState === "loading" ? "Uploading..." : "Upload document"}
      </button>
    </div>
  );
}

function DocStrip({
  document,
  indexRecord,
  indexState,
  uploadState,
  isAuthenticated,
  selectedFile,
  lastAnswer,
  error,
  replaceOpen,
  confirmReindex,
  onFileChange,
  onUpload,
  onToggleReplace,
  onReindex,
  onSetConfirmReindex,
}: {
  document: DocumentRecord | null;
  indexRecord: IndexRecord | null;
  indexState: AsyncState;
  uploadState: AsyncState;
  isAuthenticated: boolean;
  selectedFile: File | null;
  lastAnswer: AnswerResult | null;
  error: string | null;
  replaceOpen: boolean;
  confirmReindex: boolean;
  onFileChange: (event: ChangeEvent<HTMLInputElement>) => void;
  onUpload: () => void;
  onToggleReplace: () => void;
  onReindex: () => void;
  onSetConfirmReindex: (value: boolean) => void;
}) {
  const status = docStatus(indexState, indexRecord, lastAnswer);

  if (!document) {
    return (
      <aside className="h-doc">
        <div>
          <p className="h-eyebrow">Start here</p>
          <h2 className="h-doc-title">Bring in a document</h2>
          <p className="h-muted">
            Upload a PDF or DOCX. Helpmate keeps one document active at a time so
            conversation, evidence, and indexing state stay clear.
          </p>
        </div>
        <UploadDropzone
          disabled={!isAuthenticated}
          onFileChange={onFileChange}
          onUpload={onUpload}
          selectedFile={selectedFile}
          uploadState={uploadState}
        />
        {!isAuthenticated ? (
          <p className="h-note">Open the account menu and sign in to upload.</p>
        ) : null}
      </aside>
    );
  }

  return (
    <aside className={`h-doc ${indexState === "error" ? "failed" : ""}`}>
      <div>
        <p className="h-eyebrow">Document</p>
        <h2 className="h-doc-title">{document.file_name}</h2>
      </div>

      <Hairline />

      {indexState === "loading" ? (
        <div className="h-indexing">
          <div className="h-progress" />
          <p>Preparing document...</p>
        </div>
      ) : (
        <div>
          <Stat label="Pages" value={formatNumber(document.page_count)} />
          <Stat label="Chunks" value={indexRecord ? formatNumber(indexRecord.chunk_count) : "-"} />
          <Stat label="Sections" value={indexRecord ? formatNumber(indexRecord.section_count) : "-"} />
          <Stat label="Embedding" value={indexRecord?.embedding_model ?? "-"} mono />
        </div>
      )}

      <Hairline />

      <div>
        <p className="h-eyebrow">Status</p>
        <SupportPip className={status.className}>{status.label}</SupportPip>
        {indexState === "error" && error ? (
          <ErrorState title="Indexing failed" message={error} />
        ) : null}
      </div>

      <Hairline />

      <div>
        <p className="h-eyebrow">Actions</p>
        <div className="h-action-stack">
          <button
            className="h-action"
            disabled={indexState === "loading" || !isAuthenticated}
            onClick={() => onSetConfirmReindex(true)}
            type="button"
          >
            <icons.Refresh />
            Re-index
          </button>
          {confirmReindex ? (
            <div className="h-confirm">
              <p>Re-index this document? Existing answers stay visible in this session.</p>
              <div>
                <button className="h-btn h-btn-primary" onClick={onReindex} type="button">
                  Confirm
                </button>
                <button className="h-btn h-btn-ghost" onClick={() => onSetConfirmReindex(false)} type="button">
                  Cancel
                </button>
              </div>
            </div>
          ) : null}

          <button
            className="h-action"
            disabled={uploadState === "loading" || indexState === "loading" || !isAuthenticated}
            onClick={onToggleReplace}
            type="button"
          >
            <icons.Swap />
            Replace document
          </button>
          {replaceOpen ? (
            <div className="h-replace">
              <input
                accept=".pdf,.docx"
                className="h-sr-only"
                disabled={!isAuthenticated}
                id="replace-upload"
                onChange={onFileChange}
                type="file"
              />
              <label className="h-btn h-btn-ghost" htmlFor="replace-upload">
                Choose replacement
              </label>
              <span>{selectedFile?.name ?? "No file selected"}</span>
              <button
                className="h-btn h-btn-danger"
                disabled={!selectedFile || uploadState === "loading"}
                onClick={onUpload}
                type="button"
              >
                {uploadState === "loading" ? "Replacing..." : "Upload replacement"}
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </aside>
  );
}

function AskBlock({
  question,
  placeholder,
  canAsk,
  isLoading,
  askFocused,
  onQuestionChange,
  onAsk,
  onFocusChange,
}: {
  question: string;
  placeholder: string;
  canAsk: boolean;
  isLoading: boolean;
  askFocused: boolean;
  onQuestionChange: (value: string) => void;
  onAsk: () => void;
  onFocusChange: (value: boolean) => void;
}) {
  function handleKeyDown(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      if (canAsk && question.trim()) {
        onAsk();
      }
    }
  }

  return (
    <div className={`h-ask ${askFocused ? "focal-glow" : ""} ${isLoading ? "loading" : ""}`}>
      <p className="h-ask-label">Ask</p>
      <textarea
        disabled={!canAsk || isLoading}
        id="ask-textarea"
        onBlur={() => onFocusChange(false)}
        onChange={(event) => onQuestionChange(event.target.value)}
        onFocus={() => onFocusChange(true)}
        onKeyDown={handleKeyDown}
        placeholder={isLoading ? "Generating answer..." : placeholder}
        value={question}
      />
      <div className="h-ask-row">
        <span className="h-help">
          <kbd>⌘↵</kbd>
          <span>to submit</span>
        </span>
        <button
          className="h-btn h-btn-primary"
          disabled={!canAsk || !question.trim() || isLoading}
          onClick={onAsk}
          type="button"
        >
          {isLoading ? (
            <>
              <span aria-hidden="true" className="button-spinner" />
              Generating
            </>
          ) : (
            <>
              <icons.Send size={13} />
              Generate answer
            </>
          )}
        </button>
      </div>
    </div>
  );
}

function StarterChips({
  starters,
  visible,
  onPick,
}: {
  starters: string[];
  visible: boolean;
  onPick: (question: string) => void;
}) {
  if (!visible || !starters.length) {
    return null;
  }
  return (
    <div className="h-starters">
      <p className="h-eyebrow">Starter questions</p>
      <div className="h-chip-row">
        {starters.slice(0, 3).map((starter) => (
          <button className="h-chip" key={starter} onClick={() => onPick(starter)} type="button">
            {starter}
          </button>
        ))}
      </div>
    </div>
  );
}

function EmptyHero({ isAuthenticated }: { isAuthenticated: boolean }) {
  return (
    <section className="h-hero">
      <p className="h-eyebrow">Helpmate AI</p>
      <h1>
        Grounded answers with{" "}
        <span className="h-accent">visible evidence</span> and{" "}
        <span className="h-accent">zero false support</span>
      </h1>
      <div className="h-hero-steps">
        <p>
          <span>1</span>Upload a PDF or DOCX in the strip on the left.
        </p>
        <p>
          <span>2</span>Ask anything from the document. Helpmate only answers
          with what&apos;s in it.
        </p>
        <p>
          <span>3</span>See the source for every claim in the answer, beside
          the answer.
        </p>
        <p>
          <span>4</span>Ask the same question again and the cached answer
          comes back instantly.
        </p>
      </div>
      {!isAuthenticated ? (
        <p className="h-empty-line">Sign in from the account menu to start a private study session.</p>
      ) : null}
    </section>
  );
}

function CitationPill({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick?: () => void;
}) {
  if (!onClick) {
    return <span className="h-cite muted">[{label}]</span>;
  }
  return (
    <button className={`h-cite ${active ? "active" : ""}`} onClick={onClick} type="button">
      [{label}]
    </button>
  );
}

function AnswerBody({
  turn,
  highlightedCitationKey,
  onCitationClick,
}: {
  turn: QATurn;
  highlightedCitationKey: string | null;
  onCitationClick: (turnId: string, chunkId: string) => void;
}) {
  const definitions = parseDefinitionStyleAnswer(turn.answer.answer);
  if (definitions) {
    return (
      <div className="h-definition-grid">
        {definitions.map((item) => (
          <article key={item.term}>
            <p>{item.term}</p>
            <span>{item.value}</span>
          </article>
        ))}
      </div>
    );
  }

  const paragraphs = stripReferencesBlock(turn.answer.answer)
    .split(/\n\s*\n/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);

  return (
    <div className="h-answer-body">
      {(paragraphs.length ? paragraphs : [stripReferencesBlock(turn.answer.answer)]).map((paragraph, paragraphIndex) => {
        const segments = splitCitationSegments(paragraph, turn.answer.evidence);
        return (
          <p key={`${turn.id}-${paragraphIndex}`}>
            {segments.map((segment, segmentIndex) => {
              if (segment.type === "text") {
                return <span key={segmentIndex}>{segment.text}</span>;
              }
              const chunkId = segment.target?.chunkId;
              const citationKey = chunkId ? `${turn.id}:${chunkId}` : null;
              return (
                <CitationPill
                  active={citationKey === highlightedCitationKey}
                  key={`${segment.raw}-${segmentIndex}`}
                  label={segment.label}
                  onClick={chunkId ? () => onCitationClick(turn.id, chunkId) : undefined}
                />
              );
            })}
          </p>
        );
      })}
    </div>
  );
}

function TurnActionsMenu({
  open,
  onToggle,
  onClose,
  onCopyAnswer,
  onCopyCitations,
  onReAsk,
  onDelete,
}: {
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  onCopyAnswer: () => void;
  onCopyCitations: () => void;
  onReAsk: () => void;
  onDelete: () => void;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocPointer(event: globalThis.MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(event.target as Node)) {
        onClose();
      }
    }
    function onKey(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    }
    const doc = window.document;
    doc.addEventListener("mousedown", onDocPointer);
    doc.addEventListener("keydown", onKey, true);
    return () => {
      doc.removeEventListener("mousedown", onDocPointer);
      doc.removeEventListener("keydown", onKey, true);
    };
  }, [onClose, open]);

  return (
    <div className="h-turn-menu" ref={wrapRef}>
      <button
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label="Question actions"
        className="h-turn-menu-trigger"
        onClick={onToggle}
        type="button"
      >
        <icons.More />
      </button>
      {open ? (
        <div className="h-turn-menu-panel" role="menu">
          <button onClick={onCopyAnswer} role="menuitem" type="button">
            Copy answer
          </button>
          <button onClick={onCopyCitations} role="menuitem" type="button">
            Copy citations
          </button>
          <button onClick={onReAsk} role="menuitem" type="button">
            Re-ask this question
          </button>
          <button
            className="h-turn-menu-danger"
            onClick={onDelete}
            role="menuitem"
            type="button"
          >
            Delete turn
          </button>
        </div>
      ) : null}
    </div>
  );
}

function QACard({
  turn,
  streaming,
  highlightedCitationKey,
  menuOpen,
  cardRef,
  onCitationClick,
  onMenuToggle,
  onMenuClose,
  onCopyAnswer,
  onCopyCitations,
  onReAsk,
  onDeleteTurn,
}: {
  turn: QATurn;
  streaming: boolean;
  highlightedCitationKey: string | null;
  menuOpen: boolean;
  cardRef: (element: HTMLElement | null) => void;
  onCitationClick: (turnId: string, chunkId: string) => void;
  onMenuToggle: (turnId: string) => void;
  onMenuClose: () => void;
  onCopyAnswer: (turnId: string) => void;
  onCopyCitations: (turnId: string) => void;
  onReAsk: (turnId: string) => void;
  onDeleteTurn: (turnId: string) => void;
}) {
  const fullAnswerText = useMemo(
    () => stripReferencesBlock(turn.answer.answer),
    [turn.answer.answer],
  );
  const typingDuration = useMemo(() => streamingDuration(fullAnswerText), [fullAnswerText]);
  const charsTyped = useTypingProgress(fullAnswerText, typingDuration, streaming);
  const partialText = streaming ? fullAnswerText.slice(0, charsTyped) : fullAnswerText;

  const uniqueTargets = uniqueCitationTargets(turn.answer.answer, turn.answer.evidence);

  return (
    <article className={`h-qa-card${streaming ? " focal-glow streaming" : ""}`} ref={cardRef}>
      <div className="h-qa-head">
        <span className="h-qa-meta">{relativeTime(turn.askedAt)}</span>
        <TurnActionsMenu
          onClose={onMenuClose}
          onCopyAnswer={() => onCopyAnswer(turn.id)}
          onCopyCitations={() => onCopyCitations(turn.id)}
          onDelete={() => onDeleteTurn(turn.id)}
          onReAsk={() => onReAsk(turn.id)}
          onToggle={() => onMenuToggle(turn.id)}
          open={menuOpen}
        />
      </div>
      <h3>{turn.question}</h3>
      <Hairline />
      <div className="h-answer-head">
        {streaming ? (
          <SupportPip className="pulse">generating</SupportPip>
        ) : (
          <SupportPip className={turn.answer.support_status === "partial" ? "warn" : turn.answer.support_status === "unsupported" ? "muted" : ""}>
            {supportCopy(turn.answer.support_status)}
          </SupportPip>
        )}
        <span className="h-model">
          {streaming
            ? "Streaming response"
            : turn.answer.support_summary?.trim() ||
              (turn.answer.cache_status.answer_cache_hit ? "Cache hit" : "")}
        </span>
      </div>
      {streaming ? (
        <div className="h-answer-body">
          <p>
            {partialText}
            <span aria-hidden="true" className="h-caret" />
          </p>
        </div>
      ) : (
        <AnswerBody
          highlightedCitationKey={highlightedCitationKey}
          onCitationClick={onCitationClick}
          turn={turn}
        />
      )}
      {!streaming && uniqueTargets.length >= 4 ? (
        <>
          <Hairline />
          <div className="h-citation-row">
            <p>Citations - {uniqueTargets.length} sources</p>
            <div>
              {uniqueTargets.map((target) => (
                <CitationPill
                  active={highlightedCitationKey === `${turn.id}:${target.chunkId}`}
                  key={target.chunkId}
                  label={target.label}
                  onClick={() => onCitationClick(turn.id, target.chunkId)}
                />
              ))}
            </div>
          </div>
        </>
      ) : null}
      {!streaming && turn.answer.note && turn.answer.support_status !== "supported" ? (
        <p className="h-note-card">{turn.answer.note}</p>
      ) : null}
    </article>
  );
}

function PendingQACard({ question }: { question: string }) {
  return (
    <article className="h-qa-card focal-glow">
      <div className="h-qa-head">
        <span>Q - now</span>
      </div>
      <h3>{question}</h3>
      <Hairline />
      <div className="h-answer-head">
        <SupportPip className="pulse">generating</SupportPip>
      </div>
      <div className="h-answer-body">
        <p>Retrieving evidence and generating a grounded answer...</p>
      </div>
    </article>
  );
}

function Conversation({
  document,
  indexRecord,
  indexState,
  isAuthenticated,
  starters,
  question,
  turns,
  pendingQuestion,
  answerState,
  askFocused,
  highlightedCitationKey,
  streamingTurnId,
  openMenuTurnId,
  onQuestionChange,
  onAsk,
  onFocusChange,
  onPickStarter,
  onCitationClick,
  registerTurnRef,
  onMenuToggle,
  onMenuClose,
  onCopyAnswer,
  onCopyCitations,
  onReAsk,
  onDeleteTurn,
}: {
  document: DocumentRecord | null;
  indexRecord: IndexRecord | null;
  indexState: AsyncState;
  isAuthenticated: boolean;
  starters: string[];
  question: string;
  turns: QATurn[];
  pendingQuestion: string | null;
  answerState: AsyncState;
  askFocused: boolean;
  highlightedCitationKey: string | null;
  streamingTurnId: string | null;
  openMenuTurnId: string | null;
  onQuestionChange: (value: string) => void;
  onAsk: () => void;
  onFocusChange: (value: boolean) => void;
  onPickStarter: (question: string) => void;
  onCitationClick: (turnId: string, chunkId: string) => void;
  registerTurnRef: (turnId: string, element: HTMLElement | null) => void;
  onMenuToggle: (turnId: string) => void;
  onMenuClose: () => void;
  onCopyAnswer: (turnId: string) => void;
  onCopyCitations: (turnId: string) => void;
  onReAsk: (turnId: string) => void;
  onDeleteTurn: (turnId: string) => void;
}) {
  const canAsk = Boolean(isAuthenticated && document && indexRecord && indexState !== "loading" && indexState !== "error");
  const placeholder = !document
    ? "Upload a document first"
    : indexState === "loading"
      ? "Indexing - please wait"
      : starters[0] ?? "Ask anything from the document — ⌘↵ to submit";

  return (
    <main className="h-conv">
      <div className="h-conv-inner">
        {!document ? (
          <EmptyHero isAuthenticated={isAuthenticated} />
        ) : (
          <>
            <StarterChips
              onPick={onPickStarter}
              starters={starters}
              visible={Boolean(indexRecord && turns.length === 0 && !pendingQuestion)}
            />
            <div className="h-ask-group">
              <ReadModeAbstentionBanner
                answer={turns.length > 0 ? turns[turns.length - 1].answer : null}
              />
              <AskBlock
                askFocused={askFocused}
                canAsk={canAsk}
                isLoading={answerState === "loading"}
                onAsk={onAsk}
                onFocusChange={onFocusChange}
                onQuestionChange={onQuestionChange}
                placeholder={placeholder}
                question={question}
              />
            </div>
            {indexState === "loading" ? (
              <p className="h-empty-line">
                Helpmate is preparing the document. The ask box unlocks when the index is ready.
              </p>
            ) : indexState === "error" ? (
              <p className="h-empty-line">
                Indexing did not complete. Retry indexing or replace the document from the strip.
              </p>
            ) : turns.length === 0 && !pendingQuestion ? (
              <p className="h-empty-line">
                Ask anything from the document. Helpmate only answers with what is in it.
              </p>
            ) : null}
            {pendingQuestion ? <PendingQACard question={pendingQuestion} /> : null}
            <div className="h-thread">
              {turns.map((turn) => (
                <QACard
                  cardRef={(element) => registerTurnRef(turn.id, element)}
                  highlightedCitationKey={highlightedCitationKey}
                  key={turn.id}
                  menuOpen={openMenuTurnId === turn.id}
                  onCitationClick={onCitationClick}
                  onCopyAnswer={onCopyAnswer}
                  onCopyCitations={onCopyCitations}
                  onDeleteTurn={onDeleteTurn}
                  onMenuClose={onMenuClose}
                  onMenuToggle={onMenuToggle}
                  onReAsk={onReAsk}
                  streaming={streamingTurnId === turn.id}
                  turn={turn}
                />
              ))}
            </div>
          </>
        )}
      </div>
    </main>
  );
}

function EvidenceCard({
  candidate,
  index,
  turnId,
  fileName,
  debugOpen,
  highlighted,
  tagRinged,
  cardRef,
  onTagClick,
}: {
  candidate: RetrievalCandidate;
  index: number;
  turnId: string;
  fileName: string;
  debugOpen: boolean;
  highlighted: boolean;
  tagRinged: boolean;
  cardRef: (element: HTMLElement | null) => void;
  onTagClick: (turnId: string, chunkId: string) => void;
}) {
  const strength = candidateStrength(candidate);
  // useReadModeActions only — never re-renders this card from state changes.
  const { enterReadMode } = useReadModeActions();
  const handleOpenInSource = () => {
    enterReadMode(buildReadModeChunk(candidate, fileName));
  };
  return (
    <article
      className={`h-evi-card ${highlighted ? "focal-glow active" : ""}`}
      data-chunk-id={candidate.chunk_id}
      ref={cardRef}
    >
      <div className="h-evi-card-head">
        <button
          className={`h-cite h-cite-header ${tagRinged ? "active" : ""}`}
          onClick={() => onTagClick(turnId, candidate.chunk_id)}
          type="button"
        >
          {candidateLabel(candidate, index)}
        </button>
        {strength ? <span className={`h-strength ${strength}`}>{strength}</span> : null}
      </div>
      <p className="h-evi-preview">
        {renderHighlightedText(candidate.text, metadataHighlightTerms(candidate))}
      </p>
      <button
        type="button"
        className="h-source-row"
        onClick={handleOpenInSource}
        aria-label={`Open ${fileName} in source viewer`}
      >
        <span aria-hidden="true" className="h-source-arrow">↗</span>
        <span>Open in source</span>
        <span className="h-source-dot" aria-hidden="true">·</span>
        <strong>{fileName}</strong>
      </button>
      {debugOpen ? (
        <div className="h-debug-strip">
          <span><b>Score</b> {candidate.fused_score.toFixed(3)}</span>
          <span><b>Dense</b> {candidate.dense_score.toFixed(3)}</span>
          <span><b>Lexical</b> {candidate.lexical_score.toFixed(3)}</span>
          <span><b>Kind</b> {candidateKind(candidate)}</span>
        </div>
      ) : null}
    </article>
  );
}

function EvidenceEmpty({ document, pending }: { document: DocumentRecord | null; pending: boolean }) {
  if (pending) {
    return (
      <div className="h-evi-skeletons">
        <SupportPip className="pulse">Retrieving evidence</SupportPip>
        <div className="h-evi-skeleton" />
        <div className="h-evi-skeleton" />
        <div className="h-evi-skeleton" />
      </div>
    );
  }
  return (
    <div className="h-evi-empty">
      <div className="stack"><span /><span /><span /></div>
      <p>
        {document
          ? "Evidence appears here as you ask questions. Cards link to parseable citations in the answer."
          : "Evidence will appear here after a document is indexed and questioned."}
      </p>
    </div>
  );
}

function EvidenceRail({
  document,
  turns,
  pending,
  debugOpen,
  debugEnabled,
  highlightedChunkId,
  highlightedCitationKey,
  onDebugToggle,
  onEvidenceTagClick,
  registerEvidenceRef,
}: {
  document: DocumentRecord | null;
  turns: QATurn[];
  pending: boolean;
  debugOpen: boolean;
  debugEnabled: boolean;
  highlightedChunkId: string | null;
  highlightedCitationKey: string | null;
  onDebugToggle: () => void;
  onEvidenceTagClick: (turnId: string, chunkId: string) => void;
  registerEvidenceRef: (chunkId: string, element: HTMLElement | null) => void;
}) {
  const groups = [...turns].reverse();
  const evidenceCount = groups.reduce((total, turn) => total + turn.answer.evidence.length, 0);

  return (
    <aside className="h-evi">
      <div className="h-evi-head">
        <div>
          <span className="h-eyebrow">Evidence</span>
          <span className="h-evi-count">· {evidenceCount} {evidenceCount === 1 ? "chunk" : "chunks"}</span>
        </div>
        {debugEnabled ? (
          <button className={`h-debug-toggle ${debugOpen ? "on" : ""}`} onClick={onDebugToggle} type="button">
            <span>Debug</span>
            <i />
          </button>
        ) : null}
      </div>
      {groups.length || pending ? (
        <div className="h-evi-list">
          {pending ? (
            <div className="h-evi-skeletons">
              <SupportPip className="pulse">Retrieving evidence</SupportPip>
              <div className="h-evi-skeleton" />
              <div className="h-evi-skeleton" />
            </div>
          ) : null}
          {groups.map((turn) => (
            <section className="h-evi-group" key={turn.id}>
              <p className="h-evi-group-head">Q · {relativeTime(turn.askedAt)} · {turn.question.slice(0, 54)}</p>
              {turn.answer.evidence.map((candidate, index) => (
                <EvidenceCard
                  cardRef={(element) => registerEvidenceRef(candidate.chunk_id, element)}
                  candidate={candidate}
                  debugOpen={debugOpen}
                  fileName={document?.file_name ?? "Document"}
                  highlighted={highlightedChunkId === candidate.chunk_id}
                  index={index}
                  key={`${turn.id}-${candidate.chunk_id}`}
                  onTagClick={onEvidenceTagClick}
                  tagRinged={highlightedCitationKey === `${turn.id}:${candidate.chunk_id}`}
                  turnId={turn.id}
                />
              ))}
            </section>
          ))}
        </div>
      ) : (
        <EvidenceEmpty document={document} pending={pending} />
      )}
    </aside>
  );
}

function MobileEvidence({
  turn,
  fileName,
  open,
  debugOpen,
  highlightedChunkId,
  highlightedCitationKey,
  onToggle,
  onEvidenceTagClick,
}: {
  turn: QATurn;
  open: boolean;
  debugOpen: boolean;
  highlightedChunkId: string | null;
  highlightedCitationKey: string | null;
  onToggle: () => void;
  onEvidenceTagClick: (turnId: string, chunkId: string) => void;
  fileName: string;
}) {
  return (
    <div className="h-mobile-evidence">
      <button className={`h-mobile-evidence-toggle ${open ? "open" : ""}`} onClick={onToggle} type="button">
        <span>{open ? "Hide" : "Show"} evidence ({turn.answer.evidence.length})</span>
        <icons.Chevron />
      </button>
      {open ? (
        <div className="h-mobile-evidence-list">
          {turn.answer.evidence.map((candidate, index) => (
            <EvidenceCard
              cardRef={() => undefined}
              candidate={candidate}
              debugOpen={debugOpen}
              fileName={fileName}
              highlighted={highlightedChunkId === candidate.chunk_id}
              index={index}
              key={candidate.chunk_id}
              onTagClick={onEvidenceTagClick}
              tagRinged={highlightedCitationKey === `${turn.id}:${candidate.chunk_id}`}
              turnId={turn.id}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

type PaletteAction = "focus-ask" | "reindex" | "replace";

type PaletteSection = {
  heading: string;
  chunkId: string;
  pageLabel: string;
};

type PaletteResult =
  | { kind: "qa"; key: string; turnId: string; question: string; preview: string }
  | { kind: "section"; key: string; section: PaletteSection }
  | {
      kind: "action";
      key: string;
      action: PaletteAction;
      label: string;
      description: string;
    };

function paletteSectionsFromTurns(turns: QATurn[]): PaletteSection[] {
  const seen = new Set<string>();
  const sections: PaletteSection[] = [];
  for (const turn of turns) {
    for (const candidate of turn.answer.evidence) {
      const heading = metadataText(candidate, "section_heading").trim();
      if (!heading) {
        continue;
      }
      const dedupeKey = heading.toLowerCase();
      if (seen.has(dedupeKey)) {
        continue;
      }
      seen.add(dedupeKey);
      sections.push({
        heading,
        chunkId: candidate.chunk_id,
        pageLabel: metadataText(candidate, "page_label"),
      });
    }
  }
  return sections;
}

function PaletteResultRow({
  result,
  selected,
  onSelect,
  onHover,
}: {
  result: PaletteResult;
  selected: boolean;
  onSelect: () => void;
  onHover: () => void;
}) {
  let title = "";
  let preview = "";
  let badge = "";
  if (result.kind === "qa") {
    title = result.question;
    preview = result.preview;
    badge = "Q&A";
  } else if (result.kind === "section") {
    title = result.section.heading;
    preview = result.section.pageLabel || "Document section";
    badge = "Section";
  } else {
    title = result.label;
    preview = result.description;
    badge = "Action";
  }
  return (
    <button
      className={`h-palette-result${selected ? " selected" : ""}`}
      onClick={onSelect}
      onMouseEnter={onHover}
      type="button"
    >
      <span className="h-palette-result-badge">{badge}</span>
      <span className="h-palette-result-body">
        <span className="h-palette-result-title">{title}</span>
        {preview ? <span className="h-palette-result-preview">{preview}</span> : null}
      </span>
    </button>
  );
}

function CommandPalette({
  open,
  turns,
  documentLoaded,
  isAuthenticated,
  indexState,
  onClose,
  onSelectTurn,
  onSelectSection,
  onAction,
}: {
  open: boolean;
  turns: QATurn[];
  documentLoaded: boolean;
  isAuthenticated: boolean;
  indexState: AsyncState;
  onClose: () => void;
  onSelectTurn: (turnId: string) => void;
  onSelectSection: (section: PaletteSection) => void;
  onAction: (action: PaletteAction) => void;
}) {
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const overlayRef = useRef<HTMLDivElement | null>(null);

  const sections = useMemo(() => paletteSectionsFromTurns(turns), [turns]);

  const actions = useMemo<
    Array<{ action: PaletteAction; label: string; description: string }>
  >(() => {
    const list: Array<{ action: PaletteAction; label: string; description: string }> = [];
    if (documentLoaded && isAuthenticated && indexState === "ready") {
      list.push({
        action: "focus-ask",
        label: "Focus ask box",
        description: "Move the cursor to the question input.",
      });
    }
    if (documentLoaded && isAuthenticated && indexState !== "loading") {
      list.push({
        action: "reindex",
        label: "Re-index document",
        description: "Rebuild the index for the active document.",
      });
    }
    if (isAuthenticated) {
      list.push({
        action: "replace",
        label: "Replace document",
        description: "Swap the active document for a new upload.",
      });
    }
    return list;
  }, [documentLoaded, isAuthenticated, indexState]);

  const results = useMemo<PaletteResult[]>(() => {
    const q = query.trim().toLowerCase();
    const qaMatches = [...turns]
      .reverse()
      .filter((turn) => {
        if (!q) {
          return true;
        }
        return (
          turn.question.toLowerCase().includes(q) ||
          turn.answer.answer.toLowerCase().includes(q)
        );
      })
      .slice(0, 6)
      .map<PaletteResult>((turn) => ({
        kind: "qa",
        key: `qa-${turn.id}`,
        turnId: turn.id,
        question: turn.question,
        preview: stripReferencesBlock(turn.answer.answer).replace(/\s+/g, " ").slice(0, 110),
      }));

    const sectionMatches = sections
      .filter((section) => !q || section.heading.toLowerCase().includes(q))
      .slice(0, 6)
      .map<PaletteResult>((section) => ({
        kind: "section",
        key: `section-${section.chunkId}`,
        section,
      }));

    const actionMatches = actions
      .filter(
        (item) =>
          !q ||
          item.label.toLowerCase().includes(q) ||
          item.description.toLowerCase().includes(q),
      )
      .map<PaletteResult>((item) => ({
        kind: "action",
        key: `action-${item.action}`,
        action: item.action,
        label: item.label,
        description: item.description,
      }));

    return [...qaMatches, ...sectionMatches, ...actionMatches];
  }, [actions, query, sections, turns]);

  useEffect(() => {
    const raf = requestAnimationFrame(() => setSelectedIndex(0));
    return () => cancelAnimationFrame(raf);
  }, [results.length, query]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const raf = requestAnimationFrame(() => {
      setQuery("");
      setSelectedIndex(0);
      inputRef.current?.focus();
    });
    return () => cancelAnimationFrame(raf);
  }, [open]);

  const handleSelect = useCallback(
    (result: PaletteResult) => {
      if (result.kind === "qa") {
        onSelectTurn(result.turnId);
      } else if (result.kind === "section") {
        onSelectSection(result.section);
      } else {
        onAction(result.action);
      }
      onClose();
    },
    [onAction, onClose, onSelectSection, onSelectTurn],
  );

  useEffect(() => {
    if (!open) {
      return;
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (!results.length) {
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setSelectedIndex((current) => Math.min(current + 1, results.length - 1));
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setSelectedIndex((current) => Math.max(current - 1, 0));
      } else if (event.key === "Enter") {
        event.preventDefault();
        const target = results[selectedIndex];
        if (target) {
          handleSelect(target);
        }
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [handleSelect, onClose, open, results, selectedIndex]);

  if (!open) {
    return null;
  }

  function onOverlayMouseDown(event: ReactMouseEvent<HTMLDivElement>) {
    if (event.target === overlayRef.current) {
      onClose();
    }
  }

  const groupHeads: Record<PaletteResult["kind"], string> = {
    qa: "This session",
    section: "Sections",
    action: "Actions",
  };

  let lastKind: PaletteResult["kind"] | null = null;

  return (
    <div
      aria-modal="true"
      className="h-palette-overlay"
      onMouseDown={onOverlayMouseDown}
      ref={overlayRef}
      role="dialog"
    >
      <div className="h-palette-modal">
        <div className="h-palette-input-wrap">
          <icons.Search size={14} />
          <input
            aria-label="Search palette"
            className="h-palette-input"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search Q&A, sections, and actions..."
            ref={inputRef}
            type="text"
            value={query}
          />
          <kbd>ESC</kbd>
        </div>
        <div className="h-palette-results">
          {results.length === 0 ? (
            <p className="h-palette-empty">No matches in this session.</p>
          ) : (
            results.map((result, index) => {
              const showHead = result.kind !== lastKind;
              lastKind = result.kind;
              return (
                <div className="h-palette-row" key={result.key}>
                  {showHead ? (
                    <p className="h-palette-group-head">{groupHeads[result.kind]}</p>
                  ) : null}
                  <PaletteResultRow
                    onHover={() => setSelectedIndex(index)}
                    onSelect={() => handleSelect(result)}
                    result={result}
                    selected={index === selectedIndex}
                  />
                </div>
              );
            })
          )}
        </div>
        <div className="h-palette-shortcut">
          <span>↑↓ navigate</span>
          <span>↵ open</span>
          <span>esc close</span>
        </div>
      </div>
    </div>
  );
}

export function AppWorkspace({ user }: AppWorkspaceProps) {
  const debugPanelEnabled =
    process.env.NEXT_PUBLIC_ENABLE_DEBUG_PANEL === "true";
  const isAuthenticated = Boolean(user);

  const [document, setDocument] = useState<DocumentRecord | null>(null);
  const [indexRecord, setIndexRecord] = useState<IndexRecord | null>(null);
  const [answer, setAnswer] = useState<AnswerResult | null>(null);
  const [turns, setTurns] = useState<QATurn[]>([]);
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [starters, setStarters] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadState, setUploadState] = useState<AsyncState>("idle");
  const [indexState, setIndexState] = useState<AsyncState>("idle");
  const [answerState, setAnswerState] = useState<AsyncState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [askFocused, setAskFocused] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [replaceOpen, setReplaceOpen] = useState(false);
  const [confirmReindex, setConfirmReindex] = useState(false);
  const [debugOpen, setDebugOpen] = useState(false);
  const [highlightedChunkId, setHighlightedChunkId] = useState<string | null>(null);
  const [highlightedCitationKey, setHighlightedCitationKey] = useState<string | null>(null);
  const [streamingTurnId, setStreamingTurnId] = useState<string | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [openMenuTurnId, setOpenMenuTurnId] = useState<string | null>(null);
  const [mobileEvidenceOpen, setMobileEvidenceOpen] = useState<Record<string, boolean>>({});
  const evidenceRefs = useRef<Record<string, HTMLElement | null>>({});
  const turnRefs = useRef<Record<string, HTMLElement | null>>({});
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const streamTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const status = useMemo(() => docStatus(indexState, indexRecord, answer), [answer, indexRecord, indexState]);
  const evidencePending = answerState === "loading";

  function applyDocumentBundle(bundle: DocumentBundleResponse) {
    setDocument(bundle.document);
    setIndexRecord(bundle.index);
    setAnswer(null);
    setTurns([]);
    setPendingQuestion(null);
    setQuestion("");
    setSelectedFile(null);
    setError(null);
    setConfirmReindex(false);
    setReplaceOpen(false);
    setHighlightedChunkId(null);
    setHighlightedCitationKey(null);
    setStreamingTurnId(null);
    if (streamTimer.current) {
      clearTimeout(streamTimer.current);
      streamTimer.current = null;
    }
    setMobileEvidenceOpen({});
    setIndexState(bundle.index ? "ready" : "idle");
    setUploadState("ready");
  }

  async function refreshStarters(documentId: string) {
    try {
      const starterResponse = await getStarterQuestions(documentId);
      setStarters(starterResponse.questions);
    } catch {
      setStarters([]);
    }
  }

  useEffect(() => {
    let cancelled = false;

    async function restoreWorkspace() {
      if (!isAuthenticated || document) {
        return;
      }
      try {
        const workspace = await getCurrentWorkspace();
        if (cancelled || !workspace.document) {
          return;
        }
        setDocument(workspace.document);
        setIndexRecord(workspace.index);
        setIndexState(workspace.index ? "ready" : "idle");
        setUploadState("ready");
        await refreshStarters(workspace.document.document_id);
      } catch (loadError) {
        if (!cancelled) {
          setStarters([]);
          if (
            loadError instanceof ApiError &&
            loadError.status !== 404 &&
            loadError.retriable
          ) {
            notifyApiError(loadError, "load", { onRetry: restoreWorkspace });
          }
        }
      }
    }

    void restoreWorkspace();

    return () => {
      cancelled = true;
    };
  }, [document, isAuthenticated]);

  useEffect(() => {
    return () => {
      if (flashTimer.current) {
        clearTimeout(flashTimer.current);
      }
      if (streamTimer.current) {
        clearTimeout(streamTimer.current);
      }
    };
  }, []);

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    setSelectedFile(event.target.files?.[0] ?? null);
  }

  async function handleUpload() {
    if (!selectedFile) {
      notifyError("Pick a file first", "Choose a PDF or DOCX before uploading.");
      return;
    }
    if (!isAuthenticated) {
      notifyError("Sign-in required", "Sign in with Google before uploading a document.");
      return;
    }

    setError(null);
    setUploadState("loading");
    setIndexState("idle");

    try {
      const uploadedBundle = await uploadDocument(selectedFile);
      applyDocumentBundle(uploadedBundle);

      let finalBundle = uploadedBundle;
      if (!uploadedBundle.index) {
        setIndexState("loading");
        await new Promise((resolve) => window.setTimeout(resolve, 0));
        finalBundle = await buildIndex(uploadedBundle.document.document_id);
      }

      applyDocumentBundle(finalBundle);
      await refreshStarters(finalBundle.document.document_id);
    } catch (uploadError) {
      const hadDocument = Boolean(document);
      setUploadState(hadDocument ? "ready" : "idle");
      setIndexState(hadDocument ? "error" : "idle");
      if (hadDocument) {
        setError("Re-index this document or upload a new one to recover.");
      } else {
        setError(null);
      }
      notifyApiError(uploadError, "upload", {
        onRetry: () => {
          void handleUpload();
        },
      });
    }
  }

  async function handleReindex() {
    if (!document) {
      return;
    }
    setError(null);
    setIndexState("loading");
    setConfirmReindex(false);
    try {
      const bundle = await buildIndex(document.document_id);
      setDocument(bundle.document);
      setIndexRecord(bundle.index);
      setIndexState(bundle.index ? "ready" : "idle");
      await refreshStarters(bundle.document.document_id);
    } catch (reindexError) {
      setIndexState("error");
      setError("Re-index this document or upload a new one to recover.");
      notifyApiError(reindexError, "index", {
        onRetry: () => {
          void handleReindex();
        },
      });
    }
  }

  async function runAsk(submittedQuestion: string) {
    setError(null);
    setAnswerState("loading");
    setPendingQuestion(submittedQuestion);

    try {
      const response = await askQuestion(document!.document_id, submittedQuestion);
      const turn: QATurn = {
        id: makeTurnId(),
        question: submittedQuestion,
        answer: response.answer,
        askedAt: new Date(),
      };
      setAnswer(response.answer);
      setTurns((current) => [...current, turn]);
      setMobileEvidenceOpen((current) => ({ ...current, [turn.id]: false }));
      setAnswerState("ready");
      setStreamingTurnId(turn.id);
      // Auto-jump the source viewer to the first evidence of the new
      // answer when in Read Mode. setCurrentChunk is a no-op outside
      // read mode (per store logic), so calling it unconditionally is
      // safe. Abstained answers (no evidence) leave the viewer alone —
      // ReadModeAbstentionBanner surfaces the "no evidence" signal
      // above the chat input instead.
      const firstEvidence = response.answer.evidence?.[0];
      if (firstEvidence && document) {
        useReadModeStore
          .getState()
          .setCurrentChunk(buildReadModeChunk(firstEvidence, document.file_name));
      }
      const dur = streamingDuration(stripReferencesBlock(response.answer.answer));
      if (streamTimer.current) {
        clearTimeout(streamTimer.current);
      }
      streamTimer.current = setTimeout(() => {
        setStreamingTurnId((current) => (current === turn.id ? null : current));
        streamTimer.current = null;
      }, dur);
    } catch (answerError) {
      setAnswerState("idle");
      notifyApiError(answerError, "ask", {
        onRetry: () => {
          void runAsk(submittedQuestion);
        },
      });
    } finally {
      setPendingQuestion(null);
    }
  }

  async function handleAsk() {
    if (!document) {
      notifyError("Upload a document first", "Bring in a PDF or DOCX before asking.");
      return;
    }
    if (!isAuthenticated) {
      notifyError("Sign-in required", "Sign in with Google before generating answers.");
      return;
    }
    if (!indexRecord) {
      notifyError("Indexing in progress", "This document is still being prepared. Try again in a moment.");
      return;
    }
    const submittedQuestion = question.trim();
    if (!submittedQuestion) {
      notifyError("Type a question", "Enter a question before generating an answer.");
      return;
    }

    setQuestion("");
    await runAsk(submittedQuestion);
  }

  function clearFlashLater() {
    if (flashTimer.current) {
      clearTimeout(flashTimer.current);
    }
    flashTimer.current = setTimeout(() => {
      setHighlightedChunkId(null);
      setHighlightedCitationKey(null);
    }, FLASH_MS);
  }

  function handleCitationClick(turnId: string, chunkId: string) {
    // Branch on Read Mode WITHOUT subscribing — this function is called
    // from event handlers, not render, so a one-shot getState() read
    // keeps AppWorkspace out of the mode-flip subscription tree.
    const readModeActive = useReadModeStore.getState().mode === "read";
    if (readModeActive) {
      // Read Mode citation behavior: scroll the source viewer to the
      // clicked chunk. The evidence rail is hidden so there's nothing
      // to flash, and the user is already looking at the source.
      const targetTurn = turns.find((t) => t.id === turnId);
      const candidate = targetTurn?.answer.evidence.find((c) => c.chunk_id === chunkId);
      if (candidate && document) {
        useReadModeStore
          .getState()
          .setCurrentChunk(buildReadModeChunk(candidate, document.file_name));
      }
      return;
    }
    // Normal mode: existing rail-flash behavior.
    setAskFocused(false);
    setHighlightedChunkId(chunkId);
    setHighlightedCitationKey(`${turnId}:${chunkId}`);
    setMobileEvidenceOpen((current) => ({ ...current, [turnId]: true }));
    window.setTimeout(() => {
      evidenceRefs.current[chunkId]?.scrollIntoView({
        block: "center",
        behavior: "smooth",
      });
    }, 0);
    clearFlashLater();
  }

  function registerEvidenceRef(chunkId: string, element: HTMLElement | null) {
    evidenceRefs.current[chunkId] = element;
  }

  function registerTurnRef(turnId: string, element: HTMLElement | null) {
    turnRefs.current[turnId] = element;
  }

  function handleMenuToggle(turnId: string) {
    setOpenMenuTurnId((current) => (current === turnId ? null : turnId));
  }

  function handleMenuClose() {
    setOpenMenuTurnId(null);
  }

  async function copyToClipboard(value: string): Promise<boolean> {
    if (!value) {
      return false;
    }
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(value);
        return true;
      }
    } catch {
      // fall through to the legacy path below
    }
    try {
      const textarea = window.document.createElement("textarea");
      textarea.value = value;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "absolute";
      textarea.style.left = "-9999px";
      window.document.body.appendChild(textarea);
      textarea.select();
      const ok = window.document.execCommand("copy");
      window.document.body.removeChild(textarea);
      return ok;
    } catch {
      return false;
    }
  }

  function handleCopyAnswer(turnId: string) {
    setOpenMenuTurnId(null);
    const turn = turns.find((candidate) => candidate.id === turnId);
    if (!turn) {
      return;
    }
    const text = stripReferencesBlock(turn.answer.answer);
    void copyToClipboard(text).then((ok) => {
      if (ok) {
        notifySuccess("Answer copied", "The answer text is on your clipboard.");
      } else {
        notifyError("Couldn't copy the answer", "Clipboard access was blocked by the browser.");
      }
    });
  }

  function handleCopyCitations(turnId: string) {
    setOpenMenuTurnId(null);
    const turn = turns.find((candidate) => candidate.id === turnId);
    if (!turn) {
      return;
    }
    const details = turn.answer.citation_details?.length
      ? turn.answer.citation_details
      : turn.answer.evidence
          .map((candidate, index) => {
            const label = candidate.citation_label || `Source ${index + 1}`;
            return `[${index + 1}] ${label}`;
          })
          .filter(Boolean);
    if (!details.length) {
      notifyError("No citations to copy", "This answer didn't surface any citations.");
      return;
    }
    void copyToClipboard(details.join("\n")).then((ok) => {
      if (ok) {
        notifySuccess(
          "Citations copied",
          `${details.length} ${details.length === 1 ? "citation is" : "citations are"} on your clipboard.`,
        );
      } else {
        notifyError("Couldn't copy citations", "Clipboard access was blocked by the browser.");
      }
    });
  }

  function handleReAskTurn(turnId: string) {
    setOpenMenuTurnId(null);
    const turn = turns.find((candidate) => candidate.id === turnId);
    if (!turn) {
      return;
    }
    setQuestion(turn.question);
    window.setTimeout(() => {
      const textarea = window.document.getElementById("ask-textarea");
      if (textarea instanceof HTMLTextAreaElement) {
        textarea.focus();
        textarea.setSelectionRange(turn.question.length, turn.question.length);
      }
    }, 0);
  }

  function handleDeleteTurn(turnId: string) {
    setOpenMenuTurnId(null);
    const turn = turns.find((candidate) => candidate.id === turnId);
    setTurns((current) => current.filter((candidate) => candidate.id !== turnId));
    delete turnRefs.current[turnId];
    if (turn) {
      for (const candidate of turn.answer.evidence) {
        delete evidenceRefs.current[candidate.chunk_id];
      }
    }
    setStreamingTurnId((current) => (current === turnId ? null : current));
    setHighlightedCitationKey((current) => (current?.startsWith(`${turnId}:`) ? null : current));
    setMobileEvidenceOpen((current) => {
      if (!(turnId in current)) {
        return current;
      }
      const next = { ...current };
      delete next[turnId];
      return next;
    });
  }

  function handlePaletteSelectTurn(turnId: string) {
    const node = turnRefs.current[turnId];
    if (node) {
      node.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  function handlePaletteSelectSection(section: PaletteSection) {
    setHighlightedChunkId(section.chunkId);
    setHighlightedCitationKey(null);
    window.setTimeout(() => {
      evidenceRefs.current[section.chunkId]?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    }, 0);
    clearFlashLater();
  }

  function handlePaletteAction(action: PaletteAction) {
    if (action === "focus-ask") {
      window.setTimeout(() => {
        const textarea = window.document.getElementById("ask-textarea");
        if (textarea instanceof HTMLTextAreaElement) {
          textarea.focus();
        }
      }, 0);
    } else if (action === "reindex") {
      setConfirmReindex(true);
    } else if (action === "replace") {
      setReplaceOpen(true);
    }
  }

  useEffect(() => {
    // `document` here is the DOM global; the state variable above shadows the
    // name so we resolve it through `window` for the listener registration.
    const doc = window.document;
    function onKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((current) => !current);
      }
    }
    doc.addEventListener("keydown", onKeyDown);
    return () => doc.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <WorkspaceShellChrome>
      <Topbar
          accountOpen={accountOpen}
          onAccountClose={() => setAccountOpen(false)}
          onAccountToggle={() => setAccountOpen((current) => !current)}
          onPaletteOpen={() => setPaletteOpen(true)}
          user={user}
        />
        <div className="h-mobile-docbar">
          <span>
            <icons.Doc />
            {document?.file_name ?? "No document loaded"}
          </span>
          <SupportPip className={status.className}>{status.label}</SupportPip>
        </div>
        <div className="h-frame">
          <DocStrip
            confirmReindex={confirmReindex}
            document={document}
            error={error}
            indexRecord={indexRecord}
            indexState={indexState}
            isAuthenticated={isAuthenticated}
            lastAnswer={answer}
            onFileChange={handleFileChange}
            onReindex={handleReindex}
            onSetConfirmReindex={setConfirmReindex}
            onToggleReplace={() => setReplaceOpen((current) => !current)}
            onUpload={handleUpload}
            replaceOpen={replaceOpen}
            selectedFile={selectedFile}
            uploadState={uploadState}
          />
          <Conversation
            answerState={answerState}
            askFocused={askFocused}
            document={document}
            highlightedCitationKey={highlightedCitationKey}
            indexRecord={indexRecord}
            indexState={indexState}
            isAuthenticated={isAuthenticated}
            onAsk={handleAsk}
            onCitationClick={handleCitationClick}
            onCopyAnswer={handleCopyAnswer}
            onCopyCitations={handleCopyCitations}
            onDeleteTurn={handleDeleteTurn}
            onFocusChange={setAskFocused}
            onMenuClose={handleMenuClose}
            onMenuToggle={handleMenuToggle}
            onPickStarter={setQuestion}
            onQuestionChange={setQuestion}
            onReAsk={handleReAskTurn}
            openMenuTurnId={openMenuTurnId}
            pendingQuestion={pendingQuestion}
            question={question}
            registerTurnRef={registerTurnRef}
            starters={starters}
            streamingTurnId={streamingTurnId}
            turns={turns}
          />
          <EvidenceRail
            debugEnabled={debugPanelEnabled}
            debugOpen={debugOpen}
            document={document}
            highlightedChunkId={highlightedChunkId}
            highlightedCitationKey={highlightedCitationKey}
            onDebugToggle={() => setDebugOpen((current) => !current)}
            onEvidenceTagClick={handleCitationClick}
            pending={evidencePending}
            registerEvidenceRef={registerEvidenceRef}
            turns={turns}
          />
          {/* SourcePaneMount is null in normal mode and renders the read-mode
              pane when entered. Mounting it inside .h-frame (rather than as
              a sibling) lets the grid claim the column on desktop; on mobile
              CSS lifts it to a full-screen overlay via position: fixed. */}
          <SourcePaneMount />
        </div>
        <div className="h-mobile-thread-evidence">
          {turns.map((turn) => (
            <MobileEvidence
              debugOpen={debugOpen}
              fileName={document?.file_name ?? "Document"}
              highlightedChunkId={highlightedChunkId}
              highlightedCitationKey={highlightedCitationKey}
              key={turn.id}
              onEvidenceTagClick={handleCitationClick}
              onToggle={() =>
                setMobileEvidenceOpen((current) => ({
                  ...current,
                  [turn.id]: !current[turn.id],
                }))
              }
              open={Boolean(mobileEvidenceOpen[turn.id])}
              turn={turn}
            />
          ))}
        </div>
        <CommandPalette
          documentLoaded={Boolean(document)}
          indexState={indexState}
          isAuthenticated={isAuthenticated}
          onAction={handlePaletteAction}
          onClose={() => setPaletteOpen(false)}
          onSelectSection={handlePaletteSelectSection}
          onSelectTurn={handlePaletteSelectTurn}
          open={paletteOpen}
          turns={turns}
        />
    </WorkspaceShellChrome>
  );
}

// Owns the `data-read-mode` / `data-mobile-snap` attributes on .h-shell,
// the global ESC handler, and the keyboard-detection listeners that drive
// the mobile snap state machine.
//
// Subscribes to `mode` + `mobileSnap` only — both narrow selectors so this
// component re-renders solely on those two slices changing. Children that
// care about other state (currentChunk, etc.) read it directly.
function WorkspaceShellChrome({ children }: { children: ReactNode }) {
  const mode = useReadModeStatus();
  const mobileSnap = useMobileSnap();
  const { exitReadMode, setKeyboardActive } = useReadModeActions();

  // ESC to exit Read Mode.
  useEffect(() => {
    if (mode !== "read") {
      return;
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        // stopPropagation so we don't also close the command palette if it
        // happens to be open — Read Mode exit takes priority.
        event.stopPropagation();
        exitReadMode();
      }
    }
    window.document.addEventListener("keydown", onKeyDown);
    return () => window.document.removeEventListener("keydown", onKeyDown);
  }, [mode, exitReadMode]);

  // Keyboard detection — two independent signals feed the same store
  // action. setKeyboardActive is idempotent on equality, so either path
  // can win without conflict.
  //
  //  1. Focus capture on #ask-textarea — fires synchronously when the
  //     chat input gains / loses focus, BEFORE the OS keyboard animates
  //     in. This is the "preemptive" path that snaps the sheet to
  //     COMPACT before the keyboard appears, avoiding chrome jitter.
  //
  //  2. visualViewport resize — fires AFTER the keyboard animates.
  //     Catches cases where focus events don't fire (hardware keyboards,
  //     OS voice input, OS-surfaced keyboards). The 100px threshold
  //     ignores normal URL-bar collapse on scroll.
  useEffect(() => {
    if (mode !== "read") {
      return;
    }
    function onFocusChange(event: FocusEvent) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target.id !== "ask-textarea") return;
      setKeyboardActive(event.type === "focusin");
    }
    document.addEventListener("focusin", onFocusChange);
    document.addEventListener("focusout", onFocusChange);

    let removeViewport: (() => void) | null = null;
    if (typeof window !== "undefined" && window.visualViewport) {
      const vv = window.visualViewport;
      const onResize = () => {
        const gap = window.innerHeight - vv.height;
        setKeyboardActive(gap > 100);
      };
      vv.addEventListener("resize", onResize);
      removeViewport = () => vv.removeEventListener("resize", onResize);
    }

    return () => {
      document.removeEventListener("focusin", onFocusChange);
      document.removeEventListener("focusout", onFocusChange);
      if (removeViewport) removeViewport();
    };
  }, [mode, setKeyboardActive]);

  return (
    <div
      className="h-shell"
      data-read-mode={mode}
      // data-mobile-snap is only meaningful on mobile + read mode, but we
      // write it unconditionally to keep CSS selectors simple. Desktop CSS
      // ignores it.
      data-mobile-snap={mobileSnap}
    >
      {children}
    </div>
  );
}

// Branches the source viewer based on viewport: desktop two-pane embeds
// <SourcePane> in the .h-frame grid column; mobile (<=900px) renders the
// vaul-based bottom sheet via portal.
//
// The viewport branch uses a JS media query because vaul portals outside
// .h-frame and we can't CSS-toggle a portal in/out. Returning null while
// the media query is still resolving avoids a flash of the desktop pane
// on mobile — safe because SourcePaneMount is only reached after user
// interaction (post-hydration), so the matchMedia subscription has
// already fired its first tick by then.
function SourcePaneMount() {
  const mode = useReadModeStatus();
  const isMobile = useMediaQuery("(max-width: 900px)");
  if (mode !== "read") {
    return null;
  }
  if (isMobile === null) {
    return null;
  }
  return isMobile ? <MobileSourceSheet /> : <SourcePane />;
}
