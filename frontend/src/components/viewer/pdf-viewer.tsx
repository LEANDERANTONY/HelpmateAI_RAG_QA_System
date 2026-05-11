"use client";

// PdfViewer — React wrapper around pdfjs-dist's bundled PDFViewer.
//
// Responsibilities (in order of complexity):
//   1. Lazy-load pdfjs-dist + viewer modules
//   2. Mount PDFViewer + EventBus + PDFLinkService + PDFFindController
//      against our DOM containers, set the document, listen for pagesinit
//   3. On chunk change (new currentChunk in the store), run the
//      hint-scroll + find pipeline:
//        a. Parse pageLabel → 1-based page index
//        b. Scroll to hint page (fast first-paint at the right spot)
//        c. Dispatch find with the anchor prefix, highlightAll
//        d. On updatefindmatchescount, walk the ±3 ring around hintPage
//           and scroll to the closest page that has a match. Strict
//           fallback: if nothing in window, banner + stay on hint page
//   4. Surface load failures by category (auth / missing / 415 / etc.)
//
// What this component DOESN'T do:
//   • Touch the Read Mode store (props-only API, drop-in reusable)
//   • Manage the surrounding chrome (close button, page-label pill —
//     those live in SourcePane / MobileSourceSheet, which compose the
//     PdfViewer in their body slot)
//
// PDF.js gotchas worth noting:
//   • PDFViewer mounts and renders synchronously to its container, so
//     the container must be in the DOM before construction. We use
//     useEffect (not useLayoutEffect) because the dynamic import is
//     async anyway — by the time the import resolves, mount has run.
//   • setDocument(null) detaches the current PDF cleanly. Calling
//     getDocument's result.destroy() on the proxy returned earlier is
//     the canonical way to free the worker's parsed data.
//   • findController.pageMatches is populated AFTER 'updatefindmatchescount'
//     fires with a non-zero total. Empty arrays during the scan.

import { useCallback, useEffect, useRef, useState } from "react";

import {
  loadPdfDocument,
  loadPdfjs,
  PdfLoadError,
  type PdfjsModule,
} from "@/lib/pdfjs-loader";
import { buildSearchAnchor, parsePageLabel } from "@/lib/search-anchor";

import "pdfjs-dist/web/pdf_viewer.css";

type PdfViewerProps = {
  documentId: string;
  // The chunk's hint page + anchor text. chunkId is used as the
  // navigation trigger — when it changes, we re-run the scroll+find
  // pipeline without remounting the PDF.
  chunkId: string;
  pageLabel: string;
  chunkText: string;
  // Optional: caller can pass a "download original" affordance for the
  // 415 banner (legacy DOCX without rendition).
  onDownloadOriginal?: () => void;
};

// Tightened from anything[] for safety — we only access the fields
// pdfjs-dist documents.
type PdfState = {
  // PDFViewer/EventBus/etc are all instances of pdfjs classes; we hold
  // them via InstanceType so we don't leak the heavy types throughout
  // the component.
  pdfjs: PdfjsModule;
  viewer: InstanceType<PdfjsModule["PDFViewer"]>;
  eventBus: InstanceType<PdfjsModule["EventBus"]>;
  findController: InstanceType<PdfjsModule["PDFFindController"]>;
  pagesReady: boolean;
};

type LoadState = "idle" | "loading" | "ready" | "error";
// "no-match" is the find-pipeline outcome; the rest come from PdfLoadError
// kinds. PdfLoadError's "parse" / "unknown" both map to "transport" in the
// UI because the user-facing message is the same: "Couldn't load source".
type BannerKind =
  | "no-match"
  | "needs-rendition"
  | "missing"
  | "auth"
  | "transport"
  | null;

function bannerKindFromLoadError(kind: PdfLoadError["kind"]): BannerKind {
  if (kind === "needs-rendition" || kind === "missing" || kind === "auth") {
    return kind;
  }
  // "transport", "parse", "unknown" all surface as the generic "couldn't
  // load" banner. We keep the specific PdfLoadError kinds for telemetry.
  return "transport";
}

const WINDOW_RADIUS = 3;

export function PdfViewer({
  documentId,
  chunkId,
  pageLabel,
  chunkText,
  onDownloadOriginal,
}: PdfViewerProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const viewerElRef = useRef<HTMLDivElement | null>(null);
  const stateRef = useRef<PdfState | null>(null);
  // Hold the most recent chunk in a ref so the eventBus callback (which
  // closes over the initial render) reads fresh values without us having
  // to re-register handlers on every chunk change. Updated in an effect
  // because mutating a ref during render is a hooks-rule violation —
  // even though it'd technically work for our read pattern.
  const chunkRef = useRef({ pageLabel, chunkText, chunkId });
  useEffect(() => {
    chunkRef.current = { pageLabel, chunkText, chunkId };
  });

  const [loadState, setLoadState] = useState<LoadState>("idle");
  const [banner, setBanner] = useState<BannerKind>(null);
  const [errorDetail, setErrorDetail] = useState<string>("");

  // Stable callbacks declared before the mount effect so the
  // pdfjs eventBus listeners can close over them. React's
  // immutability rule rejects forward refs to function decls
  // because the closure inside the effect would always see the
  // original version. useCallback with empty deps is correct
  // here — both functions read fresh state via stateRef + chunkRef
  // and only call setState identities (which are stable).
  const handleFindResults = useCallback(
    (matchesCount: { current: number; total: number }) => {
      const state = stateRef.current;
      if (!state) return;
      const { pageLabel: label } = chunkRef.current;

      if (matchesCount.total === 0) {
        setBanner("no-match");
        return;
      }

      const hintPage = parsePageLabel(label);
      const hintIdx = hintPage - 1;
      const pageMatches = state.findController.pageMatches as unknown as number[][];
      if (!Array.isArray(pageMatches)) {
        setBanner("no-match");
        return;
      }

      // Expanding ring [0, +1, -1, +2, -2, +3, -3]. First page in
      // ring order with a non-empty match list wins — biases toward
      // the hint page while tolerating up to ±3 pages of DOCX→PDF drift.
      const order: number[] = [0];
      for (let d = 1; d <= WINDOW_RADIUS; d++) {
        order.push(d, -d);
      }
      for (const dir of order) {
        const idx = hintIdx + dir;
        if (idx < 0 || idx >= pageMatches.length) continue;
        const matches = pageMatches[idx];
        if (matches && matches.length > 0) {
          try {
            state.viewer.scrollPageIntoView({ pageNumber: idx + 1 });
          } catch {
            /* fall through to banner */
          }
          return;
        }
      }

      setBanner("no-match");
    },
    [],
  );

  const navigateToCurrent = useCallback(() => {
    const state = stateRef.current;
    if (!state || !state.pagesReady) return;
    const { pageLabel: label, chunkText: text } = chunkRef.current;

    setBanner(null);

    const hintPage = parsePageLabel(label);
    const pageCount = state.viewer.pagesCount || 1;
    const clamped = Math.max(1, Math.min(hintPage, pageCount));
    state.viewer.currentPageNumber = clamped;

    const anchor = buildSearchAnchor(text);
    if (!anchor) {
      setBanner("no-match");
      return;
    }
    state.eventBus.dispatch("find", {
      source: state.eventBus,
      type: "",
      query: anchor,
      caseSensitive: false,
      entireWord: false,
      phraseSearch: true,
      highlightAll: true,
      findPrevious: false,
      matchDiacritics: false,
    });
  }, []);

  // Effect 1 — mount PDF.js viewer + load the document. Runs once per
  // documentId. The chunk-change navigation lives in a separate effect
  // so re-jumping doesn't tear down the PDF.
  useEffect(() => {
    if (!documentId) return;
    let cancelled = false;
    // Resetting load state when documentId changes is exactly what
    // set-state-in-effect is for — we're synchronising React with the
    // external system (the pdfjs worker fetch) at the moment the input
    // changes. The lint default is conservative; disable here only.
    /* eslint-disable react-hooks/set-state-in-effect */
    setLoadState("loading");
    setBanner(null);
    setErrorDetail("");
    /* eslint-enable react-hooks/set-state-in-effect */

    (async () => {
      const container = containerRef.current;
      const viewerEl = viewerElRef.current;
      if (!container || !viewerEl) {
        return;
      }

      // Variable name avoids `module` — Next.js linter flags it as a
      // CommonJS reserved name even in ESM contexts.
      let pdfjs: PdfjsModule;
      try {
        pdfjs = await loadPdfjs();
      } catch (err) {
        if (cancelled) return;
        setLoadState("error");
        setErrorDetail(err instanceof Error ? err.message : "PDF.js failed to load");
        return;
      }
      if (cancelled) return;

      const eventBus = new pdfjs.EventBus();
      const linkService = new pdfjs.PDFLinkService({ eventBus });
      const findController = new pdfjs.PDFFindController({
        eventBus,
        linkService,
      });
      const viewer = new pdfjs.PDFViewer({
        container,
        viewer: viewerEl,
        eventBus,
        linkService,
        findController,
      });
      linkService.setViewer(viewer);

      // pagesinit fires once the viewer knows the page count and can
      // accept currentPageNumber / find dispatches. Before this fires,
      // navigation calls are silently dropped.
      const onPagesInit = () => {
        if (cancelled) return;
        if (stateRef.current) {
          stateRef.current.pagesReady = true;
        }
        setLoadState("ready");
        // First navigation uses the chunk that was set when the viewer
        // was constructed. Subsequent navigations are driven by effect 2.
        navigateToCurrent();
      };

      // Type narrowed via a cast so we don't need to import the
      // FindController match-count event shape into our generic
      // PdfjsModule type.
      const onMatches = (payload: { matchesCount: { current: number; total: number } }) => {
        if (cancelled) return;
        handleFindResults(payload.matchesCount);
      };

      eventBus.on("pagesinit", onPagesInit);
      eventBus.on("updatefindmatchescount", onMatches);

      stateRef.current = {
        pdfjs,
        viewer,
        eventBus,
        findController,
        pagesReady: false,
      };

      try {
        const pdf = await loadPdfDocument(`/api/documents/${documentId}/file`);
        if (cancelled) {
          pdf.destroy();
          return;
        }
        viewer.setDocument(pdf);
        linkService.setDocument(pdf);
      } catch (err) {
        if (cancelled) return;
        const error = err instanceof PdfLoadError ? err : null;
        if (error) {
          setBanner(bannerKindFromLoadError(error.kind));
          setErrorDetail(error.message);
        } else {
          setBanner("transport");
          setErrorDetail(err instanceof Error ? err.message : "Couldn't load source PDF");
        }
        setLoadState("error");
      }
    })();

    return () => {
      cancelled = true;
      const state = stateRef.current;
      if (state) {
        // Detach the document — pdfjs's viewer accepts null at runtime to
        // release its internal references and trigger the worker to free
        // the parsed PDF. The TS types declare the param non-null, so we
        // cast through `unknown` rather than synthesising an empty
        // PDFDocumentProxy. setDocument(null) is the documented detach
        // hook in pdfjs-dist.
        try {
          (state.viewer.setDocument as (doc: unknown) => void)(null);
        } catch {
          /* viewer may already be torn down by react strict-mode */
        }
        stateRef.current = null;
      }
    };
    // handleFindResults and navigateToCurrent are stable (useCallback
    // with empty deps), so including them here is purely to satisfy
    // the linter — the effect still only re-runs when documentId changes.
  }, [documentId, handleFindResults, navigateToCurrent]);

  // Effect 2 — re-navigate when the chunk changes (new answer, citation
  // pill click while in read mode). Cheap because the PDF is already
  // loaded; just re-runs the scroll + find pipeline.
  //
  // We intentionally depend only on chunkId. pageLabel / chunkText are
  // read from chunkRef inside navigateToCurrent, so we always see the
  // latest values without re-running this effect on prop identity
  // churn. chunkId being a content fingerprint guarantees a fresh chunk
  // produces a fresh id.
  useEffect(() => {
    // Synchronising the PDF viewer's scroll position with the
    // current chunk is exactly the "update external system from
    // React state" pattern the lint message documents as valid —
    // it's flagging false-positive because setBanner is called
    // inside navigateToCurrent. Suppress at the boundary.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    navigateToCurrent();
  }, [chunkId, navigateToCurrent]);

  const bannerNode = renderBanner(banner, errorDetail, pageLabel, onDownloadOriginal);

  return (
    <div className="h-pdf-viewer" data-state={loadState}>
      {bannerNode}
      {/* The PDF.js viewer needs both a scrollable container and an
          inner `.pdfViewer` element. The container is the scrollable
          surface; the inner element is where pdfjs appends page DOM.
          Both classnames are dictated by pdf_viewer.css. */}
      <div
        ref={containerRef}
        className="h-pdf-container pdfViewerContainer"
        // tabIndex makes the container focusable so keyboard scroll
        // works without clicking inside.
        tabIndex={0}
      >
        <div ref={viewerElRef} className="pdfViewer" />
      </div>
      {loadState === "loading" ? <PdfLoadingSkeleton /> : null}
    </div>
  );
}

function renderBanner(
  kind: BannerKind,
  detail: string,
  pageLabel: string,
  onDownloadOriginal: (() => void) | undefined,
) {
  if (kind === null) return null;
  const hintPage = parsePageLabel(pageLabel);
  if (kind === "no-match") {
    return (
      <div className="h-pdf-banner h-pdf-banner-soft" role="status">
        Couldn&apos;t locate exact passage on Page {hintPage} or nearby.
      </div>
    );
  }
  if (kind === "needs-rendition") {
    return (
      <div className="h-pdf-banner h-pdf-banner-warn" role="alert">
        <span>Viewer needs a PDF rendition for this DOCX.</span>
        {onDownloadOriginal ? (
          <button type="button" className="h-pdf-banner-action" onClick={onDownloadOriginal}>
            Download original
          </button>
        ) : null}
      </div>
    );
  }
  if (kind === "missing") {
    return (
      <div className="h-pdf-banner h-pdf-banner-warn" role="alert">
        Source no longer available — workspace may have expired.
      </div>
    );
  }
  if (kind === "auth") {
    return (
      <div className="h-pdf-banner h-pdf-banner-warn" role="alert">
        Session expired. Reload the page to continue reading.
      </div>
    );
  }
  return (
    <div className="h-pdf-banner h-pdf-banner-warn" role="alert">
      Couldn&apos;t load source PDF{detail ? `: ${detail}` : ""}.
    </div>
  );
}

function PdfLoadingSkeleton() {
  return (
    <div className="h-pdf-loading" role="status" aria-label="Loading PDF">
      <div className="h-source-skeleton">
        <div className="h-source-skeleton-bar pulse" />
        <div className="h-source-skeleton-bar pulse" style={{ width: "85%" }} />
        <div className="h-source-skeleton-bar pulse" style={{ width: "72%" }} />
        <div className="h-source-skeleton-bar pulse" style={{ width: "90%" }} />
        <p className="h-source-skeleton-label">Loading PDF…</p>
      </div>
    </div>
  );
}
