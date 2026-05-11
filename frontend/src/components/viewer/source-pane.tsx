"use client";

// SourcePane — the right-hand pane when in Read Mode on desktop, the
// full-screen overlay on mobile. One mount point in the DOM; CSS handles
// viewport-specific layout (position, size, which chrome affordances are
// visible). Doing it with CSS instead of a JS viewport-detect avoids SSR
// hydration mismatches and means resize transitions don't need any
// JS-driven re-render.
//
// Chrome shape:
//   [‹ Back to answer]   (mobile only)
//   [Source · filename]  (desktop only)
//   ...spacer...
//   [page-label pill]
//   [×]
//
// Stage 3a renders a loading skeleton in the body slot plus a small debug
// strip showing the wired chunk metadata — that's how we verify state
// plumbing without the actual PDF.js viewer. Stage 3b replaces the body
// with the PDF.js mount; the chrome stays put.

import { useReadMode } from "@/lib/read-mode-state";

export function SourcePane() {
  const { currentChunk, exitReadMode } = useReadMode();

  // Defensive empty frame — shouldn't happen because the reducer requires
  // a chunk on `enter`, but the type is nullable so we don't crash if a
  // future caller misuses the API.
  if (!currentChunk) {
    return (
      <section className="h-source-pane" aria-label="Source viewer">
        <header className="h-source-chrome">
          <button
            type="button"
            className="h-source-close"
            aria-label="Close source viewer"
            onClick={exitReadMode}
          >
            <CloseGlyph />
          </button>
        </header>
        <div className="h-source-body" />
      </section>
    );
  }

  return (
    <section className="h-source-pane" aria-label="Source viewer">
      <header className="h-source-chrome">
        <button
          type="button"
          className="h-source-back"
          onClick={exitReadMode}
          aria-label="Back to answer"
        >
          <ChevronGlyph />
          <span>Back to answer</span>
        </button>
        <div className="h-source-meta">
          <span className="h-source-eyebrow">Source</span>
          <span className="h-source-filename" title={currentChunk.fileName}>
            {currentChunk.fileName}
          </span>
        </div>
        <div className="h-source-chrome-spacer" aria-hidden />
        <span className="h-source-page-pill" title={`Hint: ${currentChunk.pageLabel}`}>
          {currentChunk.pageLabel || "Document"}
        </span>
        <button
          type="button"
          className="h-source-close"
          aria-label="Exit Read Mode"
          onClick={exitReadMode}
        >
          <CloseGlyph />
        </button>
      </header>

      <div className="h-source-body">
        {/* Stage 3a placeholder. Stage 3b replaces this with the PDF.js
            viewer mount. The debug strip below confirms state plumbing
            reaches the pane — it goes away in 3b. */}
        <SourceLoadingSkeleton />
        <div className="h-source-debug" aria-hidden>
          <span>chunk_id</span>
          <code>{currentChunk.chunkId}</code>
          <span>page_label</span>
          <code>{currentChunk.pageLabel}</code>
          <span>text_preview</span>
          <code>
            {currentChunk.chunkText.slice(0, 140)}
            {currentChunk.chunkText.length > 140 ? "…" : ""}
          </code>
        </div>
      </div>
    </section>
  );
}

function SourceLoadingSkeleton() {
  return (
    <div className="h-source-skeleton" role="status" aria-label="Source loading">
      <div className="h-source-skeleton-bar pulse" />
      <div className="h-source-skeleton-bar pulse" style={{ width: "85%" }} />
      <div className="h-source-skeleton-bar pulse" style={{ width: "72%" }} />
      <div className="h-source-skeleton-bar pulse" style={{ width: "90%" }} />
      <div className="h-source-skeleton-bar pulse" style={{ width: "60%" }} />
      <p className="h-source-skeleton-label">Source loading…</p>
    </div>
  );
}

function CloseGlyph() {
  return (
    <svg aria-hidden="true" height="16" viewBox="0 0 24 24" width="16">
      <path d="M6 6l12 12M18 6l-12 12" />
    </svg>
  );
}

function ChevronGlyph() {
  return (
    <svg aria-hidden="true" height="16" viewBox="0 0 24 24" width="16">
      <path d="M15 6l-6 6 6 6" />
    </svg>
  );
}
