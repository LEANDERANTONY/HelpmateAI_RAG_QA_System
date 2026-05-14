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

import { useCallback, useEffect, useRef, useState } from "react";

import { PageNav } from "@/components/viewer/page-nav";
import { PdfViewer, type PdfViewerHandle } from "@/components/viewer/pdf-viewer";
import { useCurrentChunk, useReadModeActions } from "@/lib/read-mode-state";
import { parsePageLabel } from "@/lib/search-anchor";

export function SourcePane() {
  // Narrow subscriptions: re-render when the chunk changes (auto-jump on
  // new answer in Stage 3c will mutate currentChunk in place), but the
  // actions object stays reference-stable so the close handlers don't
  // churn between renders.
  const currentChunk = useCurrentChunk();
  const { exitReadMode } = useReadModeActions();

  // Track the viewer's actually-visible page so the page-pill reflects
  // where the user is, not the original hint. We key the value on the
  // current chunkId so a chunk switch naturally resets to null without
  // an effect — set-state-in-effect rule false-positives on the
  // alternative pattern. The pill falls back to the hint page label
  // until pdfjs reports the first pagechanging event.
  const [pageState, setPageState] = useState<{ chunkId: string; page: number } | null>(null);
  const visiblePage =
    pageState && pageState.chunkId === currentChunk?.chunkId ? pageState.page : null;
  const handlePageChange = (page: number) => {
    if (!currentChunk) return;
    setPageState({ chunkId: currentChunk.chunkId, page });
  };

  // Total page count comes from pdfjs once it parses the doc. Held in
  // local state so PageNav can render "N / total" reactively (the
  // imperative pageCount() on the viewer ref doesn't trigger re-render).
  const [totalPages, setTotalPages] = useState(0);
  const handleTotalPagesChange = useCallback((count: number) => {
    setTotalPages(count);
  }, []);

  // Imperative ref into the viewer for parent-driven scroll. PageNav
  // calls scrollToPage; we don't expose anything else.
  const viewerRef = useRef<PdfViewerHandle | null>(null);
  const handleJumpToPage = useCallback((pageNumber: number) => {
    viewerRef.current?.scrollToPage(pageNumber);
  }, []);

  // Focus management — when the pane mounts (Read Mode entered),
  // move keyboard focus to the close button so users have an obvious
  // exit route. We use a ref + effect (not autoFocus) so re-entry
  // after exit re-focuses too.
  const closeBtnRef = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    // Tiny delay so the slide-in animation can start before focus
    // jumps — instant focus during the transform was causing the
    // ring to flash mid-animation.
    const t = window.setTimeout(() => closeBtnRef.current?.focus(), 50);
    return () => window.clearTimeout(t);
  }, []);

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
        <PageNav
          currentPage={visiblePage ?? parsePageLabel(currentChunk.pageLabel)}
          totalPages={totalPages}
          onJump={handleJumpToPage}
        />
        <button
          ref={closeBtnRef}
          type="button"
          className="h-source-close"
          aria-label="Exit Read Mode"
          onClick={exitReadMode}
        >
          <CloseGlyph />
        </button>
      </header>

      <div className="h-source-body">
        <PdfViewer
          ref={viewerRef}
          documentId={currentChunk.documentId}
          chunkId={currentChunk.chunkId}
          pageLabel={currentChunk.pageLabel}
          chunkText={currentChunk.chunkText}
          onPageChange={handlePageChange}
          onTotalPagesChange={handleTotalPagesChange}
        />
      </div>
    </section>
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
