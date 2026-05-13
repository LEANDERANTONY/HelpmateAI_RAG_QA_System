"use client";

// PageNav — prev/next + editable "N / total" pill used by SourcePane
// (desktop chrome) and MobileSourceSheet (mobile chrome).
//
// The composite is identical at both viewports; sizing differences are
// handled by CSS (.h-source-pagenav has size variants when nested in
// mobile chrome). Keeping the React markup unified avoids the drift
// risk of two near-copies.
//
// State ownership:
//   • currentPage, totalPages: owned by the parent (SourcePane /
//     MobileSourceSheet), passed in as props. Parent reads currentPage
//     from PdfViewer's onPageChange and totalPages from
//     onTotalPagesChange.
//   • editing, draft: local to this component. Lifecycle is short
//     (focus → type → Enter or Esc/blur), no reason to bubble up.
//
// Renders nothing when totalPages is 0 (pdfjs hasn't reported the
// page count yet). The caller doesn't need to gate.

import { useEffect, useRef, useState } from "react";

type PageNavProps = {
  currentPage: number;
  totalPages: number;
  onJump: (pageNumber: number) => void;
};

export function PageNav({ currentPage, totalPages, onJump }: PageNavProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Autofocus + select-all when entering edit mode so the user can
  // immediately type-replace the current page number without first
  // selecting it. useEffect (not autoFocus prop) so subsequent re-
  // entries also focus, not just the first render.
  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  if (totalPages === 0) {
    return null;
  }

  const atFirst = currentPage <= 1;
  const atLast = currentPage >= totalPages;

  const startEdit = () => {
    setDraft(String(currentPage));
    setEditing(true);
  };

  const commit = () => {
    const n = parseInt(draft, 10);
    if (Number.isFinite(n) && n >= 1) {
      // Clamp at the caller side (PdfViewer.scrollToPage clamps too).
      // Spec: "Type number > totalPages → clamps to last page."
      onJump(Math.min(n, totalPages));
    }
    setEditing(false);
  };

  return (
    <div className="h-source-pagenav">
      <button
        type="button"
        className="h-source-pagenav-step"
        onClick={() => onJump(currentPage - 1)}
        disabled={atFirst}
        aria-label="Previous page"
        title="Previous page"
      >
        ‹
      </button>
      {editing ? (
        <input
          ref={inputRef}
          type="text"
          inputMode="numeric"
          className="h-source-pagenav-input"
          value={draft}
          onChange={(e) => setDraft(e.target.value.replace(/\D/g, ""))}
          onBlur={() => setEditing(false)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commit();
            } else if (e.key === "Escape") {
              e.preventDefault();
              setEditing(false);
            }
          }}
          aria-label={`Jump to page (1 - ${totalPages})`}
        />
      ) : (
        <button
          type="button"
          className="h-source-pagenav-pill"
          onClick={startEdit}
          aria-label={`Page ${currentPage} of ${totalPages}, click to jump`}
          title="Click to jump to a page"
        >
          {currentPage} / {totalPages}
        </button>
      )}
      <button
        type="button"
        className="h-source-pagenav-step"
        onClick={() => onJump(currentPage + 1)}
        disabled={atLast}
        aria-label="Next page"
        title="Next page"
      >
        ›
      </button>
    </div>
  );
}
