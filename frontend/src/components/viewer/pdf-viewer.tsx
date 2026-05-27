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

import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";

import { API_BASE_URL } from "@/lib/api";
import {
  loadPdfDocument,
  loadPdfjs,
  PdfLoadError,
  type PdfjsModule,
} from "@/lib/pdfjs-loader";
import { buildSearchAnchors, parsePageLabel } from "@/lib/search-anchor";

import "pdfjs-dist/web/pdf_viewer.css";

type PdfViewerProps = {
  documentId: string;
  // The chunk's hint page + anchor text. chunkId is used as the
  // navigation trigger — when it changes, we re-run the scroll+find
  // pipeline without remounting the PDF.
  chunkId: string;
  pageLabel: string;
  chunkText: string;
  // Optional: notify the parent when the visible page changes (manual
  // scroll, find-driven navigation, or auto-jump). Used to keep the
  // page-pill in the surrounding chrome live rather than stuck on the
  // hint page.
  onPageChange?: (pageNumber: number) => void;
  // Optional: notify the parent when pdfjs knows the document's total
  // page count (fired once at pagesinit). Lets the chrome's page-nav
  // controls render "N / total" instead of N alone, and gate disabled
  // states for prev/next.
  onTotalPagesChange?: (totalPages: number) => void;
};

// Imperative handle exposed via forwardRef. Lets parent chrome
// (SourcePane / MobileSourceSheet) drive scroll without re-running
// the find pipeline — manual page nav must not fight the anchor
// highlight from the chunk-driven find.
export type PdfViewerHandle = {
  scrollToPage: (pageNumber: number) => void;
  pageCount: () => number;
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
  // Tracks container width changes (mobile snap, orientation, desktop
  // pane resize) so we can re-apply the page-width fit. Disconnected
  // alongside the viewer in the effect's cleanup.
  resizeObserver: ResizeObserver;
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

// Small delay after textlayerrendered before we query for `.highlight`.
// The find controller paints highlight spans asynchronously after the
// text layer renders; without this buffer the querySelector lands too
// early and we fall back to page-top scroll. Empirically ~50ms is
// enough on a fast laptop, doubled to be conservative.
const HIGHLIGHT_PAINT_DELAY_MS = 60;

// Scroll a specific page's first match highlight into the centre of
// the viewport. pdfjs's public scrollMatchIntoView requires both the
// highlight DOM element and the controller's internal _selected state
// to match — neither of which we have from outside. The workaround is
// to drive page rendering via scrollPageIntoView, then wait for the
// text layer to render and locate the .highlight span ourselves.
//
// Two scrolls happen in quick succession:
//   1. scrollPageIntoView lands the user near the page top (and
//      triggers rendering if the page wasn't in viewport)
//   2. After textlayerrendered fires for the same page, we scroll
//      the .highlight span to {block: 'center'} via native API
//
// CSS scroll-behavior:smooth on .h-pdf-container coalesces these into
// one motion — the browser's scroll engine overrides the first
// target with the second before either animation completes. End
// result: the match lands centred, no visible "land at top then drift"
// double-scroll. Falls back to page-top scroll if the highlight can't
// be located (rare; usually means text-layer hasn't fully painted).
function scrollToMatchOnPage(
  state: {
    viewer: { scrollPageIntoView: (opts: { pageNumber: number }) => void; getPageView: (idx: number) => unknown };
    eventBus: { on: (name: string, fn: (payload: { pageNumber: number }) => void) => void; off: (name: string, fn: (payload: { pageNumber: number }) => void) => void };
  },
  pageIndex: number,
) {
  const pageNumber = pageIndex + 1;

  const centerOnHighlight = (): boolean => {
    const pageView = state.viewer.getPageView(pageIndex) as { div?: HTMLElement } | null | undefined;
    if (!pageView?.div) return false;
    const highlight = pageView.div.querySelector(".textLayer .highlight");
    if (highlight instanceof HTMLElement) {
      highlight.scrollIntoView({ block: "center", behavior: "smooth" });
      return true;
    }
    return false;
  };

  // Kick the page render / scroll. If the page is already rendered
  // (in viewport), this is effectively a no-op for the scroll engine —
  // and the immediate centerOnHighlight check below catches that case
  // without waiting for the event.
  state.viewer.scrollPageIntoView({ pageNumber });

  if (centerOnHighlight()) return;

  // Listen once for textlayerrendered on this page, then try again
  // after a paint buffer. The listener auto-removes itself on first
  // matching event; if a different page renders first we ignore it.
  const onRendered = (payload: { pageNumber: number }) => {
    if (payload.pageNumber !== pageNumber) return;
    state.eventBus.off("textlayerrendered", onRendered);
    window.setTimeout(centerOnHighlight, HIGHLIGHT_PAINT_DELAY_MS);
  };
  state.eventBus.on("textlayerrendered", onRendered);
}

// D1: with highlightAll the find controller paints the anchor on
// EVERY page where it occurs, document-wide. We want the highlight
// scoped to the single page handleFindResults resolved to. Removing
// pdfjs's highlight classes on a page neutralises the paint there
// (pdf_viewer.css styles `.highlight`); a persistent
// textlayerrendered listener re-applies this for pages that render
// or repaint later (pdfjs repaints matches on every text-layer
// render). The resolved page is never passed here, so
// scrollToMatchOnPage's `.textLayer .highlight` lookup still works.
function clearFindHighlightsOnPage(
  viewer: { getPageView: (idx: number) => unknown },
  pageNumber: number,
) {
  const pageView = viewer.getPageView(pageNumber - 1) as
    | { div?: HTMLElement }
    | null
    | undefined;
  const root = pageView?.div;
  if (!root) return;
  root.querySelectorAll(".textLayer .highlight").forEach((el) => {
    el.classList.remove("highlight", "selected", "begin", "end", "middle");
  });
}

export const PdfViewer = forwardRef<PdfViewerHandle, PdfViewerProps>(function PdfViewer(
  {
    documentId,
    chunkId,
    pageLabel,
    chunkText,
    onPageChange,
    onTotalPagesChange,
  },
  ref,
) {
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

  // Same ref trick for the onPageChange callback so the pagechanging
  // listener can call the latest prop without re-registration churn.
  const onPageChangeRef = useRef(onPageChange);
  useEffect(() => {
    onPageChangeRef.current = onPageChange;
  });

  const onTotalPagesChangeRef = useRef(onTotalPagesChange);
  useEffect(() => {
    onTotalPagesChangeRef.current = onTotalPagesChange;
  });

  // D1: the 1-based page handleFindResults resolved the anchor to.
  // null = not resolved yet — during the find scan we deliberately
  // let pdfjs paint doc-wide and only scope once a page is chosen.
  // Read by the persistent textlayerrendered listener so pages that
  // render/repaint after resolution stay scoped too.
  const resolvedPageRef = useRef<number | null>(null);

  // Imperative handle for parent-driven scroll. Critically, scrollToPage
  // ONLY moves the viewport — it must not dispatch find or touch chunk
  // state. Manual page nav and the anchor-driven find pipeline operate
  // independently; the anchor highlight stays painted wherever the last
  // find dispatch left it.
  useImperativeHandle(
    ref,
    () => ({
      scrollToPage(pageNumber: number) {
        const state = stateRef.current;
        if (!state || !state.pagesReady) return;
        const total = state.viewer.pagesCount || 1;
        const clamped = Math.max(1, Math.min(pageNumber, total));
        state.viewer.currentPageNumber = clamped;
        // Optimistic page-pill update. scroll-behavior:smooth on the
        // PDF container causes pdfjs to coalesce pagechanging events —
        // a click during an in-flight smooth scroll lands the
        // currentPageNumber setter (PDF moves) but the event for the
        // intermediate page doesn't fire until the scroll settles,
        // and rapid clicks can drop intermediate events entirely.
        // Firing onPageChange synchronously here keeps the page-pill
        // in sync with the click. If pagechanging fires later for
        // the same page, setVisiblePage is called with the identical
        // value — idempotent, no double-render.
        onPageChangeRef.current?.(clamped);
      },
      pageCount() {
        return stateRef.current?.viewer.pagesCount ?? 0;
      },
    }),
    [],
  );

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

      // Page-jump policy: TRUST the citation's hint page as the page
      // to land the user on. Earlier this ran an expanding-ring search
      // ([0, +1, -1, +2, -2, +3, -3]) and scrolled to the FIRST page
      // with any matches — which silently overrode the hint when a
      // common phrase from the chunk happened to also occur on page 1
      // (or any earlier page than the cited one). Result: users tapped
      // a "Page 2" citation and the PDF viewer scrolled them to page 1.
      //
      // PDF.js also dispatches `updatefindmatchescount` PROGRESSIVELY
      // as it scans linearly from page 1 — so the ring's "first match
      // wins" check would see page 1 matches before the hint page had
      // even been scanned, locking in the wrong page on every fire of
      // this handler.
      //
      // New behavior:
      //   * Highlight scoping ALWAYS scopes to the hint page — paint
      //     stays on the cited page even if matches exist elsewhere
      //     (those used to leak as a brief flash on adjacent pages
      //     while the scan progressed).
      //   * If the hint page has matches, scroll-center on its first
      //     match (a refinement on top of the hint-page jump that
      //     navigateToCurrent already did).
      //   * If the hint page has NO matches in this dispatch, leave the
      //     user on the hint page without scrolling. The ``no-match``
      //     banner surfaces from the total-zero branch above only when
      //     the document truly contains no matches anywhere; intermediate
      //     "no match on hint page yet" states stay silent so progressive
      //     scans don't briefly flash a banner that then disappears.
      try {
        const total = state.viewer.pagesCount || 0;
        // Resolve to the hint page regardless of whether matches landed
        // there; this is the page we want highlights scoped to.
        const resolvedPage = Math.max(1, Math.min(hintPage, total || hintPage));
        resolvedPageRef.current = resolvedPage;
        for (let p = 1; p <= total; p++) {
          if (p !== resolvedPage) clearFindHighlightsOnPage(state.viewer, p);
        }
        const hintMatches =
          hintIdx >= 0 && hintIdx < pageMatches.length ? pageMatches[hintIdx] : null;
        if (hintMatches && hintMatches.length > 0) {
          scrollToMatchOnPage(state, hintIdx);
          setBanner(null);
        }
        // No matches on the hint page (yet) — silently keep the user on
        // the cited page. navigateToCurrent already set
        // currentPageNumber=hintPage before dispatching find, so they're
        // exactly where the citation said to go.
      } catch {
        /* scroll failure leaves any prior banner intact */
      }
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

    // D1: new search — forget the previous resolution and wipe any
    // lingering highlightAll paint so the prior chunk's highlights
    // don't persist while the new scan runs. resolvedPageRef stays
    // null until handleFindResults resolves the new target page.
    resolvedPageRef.current = null;
    const totalPages = state.viewer.pagesCount || 0;
    for (let p = 1; p <= totalPages; p++) {
      clearFindHighlightsOnPage(state.viewer, p);
    }

    const anchors = buildSearchAnchors(text);
    if (anchors.length === 0) {
      setBanner("no-match");
      return;
    }
    state.eventBus.dispatch("find", {
      source: state.eventBus,
      type: "",
      // pdfjs v5 accepts string | string[]; an array highlights every
      // phrase (D2.2 — the highlight spans the chunk, not just its
      // first ~80 chars). Single anchor stays a plain string.
      query: anchors.length === 1 ? anchors[0] : anchors,
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
        // Without a banner kind, loadState="error" hides the loading
        // skeleton but renders nothing in its place — the user sees a
        // black void. Categorize as "transport" so the generic "The
        // source PDF didn't load" banner surfaces with the actual
        // error in parens.
        setLoadState("error");
        setBanner("transport");
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

      // Fit the PDF to its container. Two strategies by viewport:
      //
      //  Desktop  → pdfjs's "page-width" sentinel. It reserves a
      //             hardcoded SCROLLBAR_PADDING (40px) before deriving
      //             the scale, which is correct there: the desktop
      //             pane shows a real scrollbar, and the resulting
      //             centred-page-on-dark-backdrop is the intended
      //             document-reader look.
      //
      //  Mobile   → an EXPLICIT numeric scale = clientWidth /
      //             nativePageWidth. The sheet scrolls by touch
      //             (overlay scrollbar; the custom one is hidden in
      //             CSS at <=900px), so reserving pdfjs's 40px just
      //             leaves a dark gutter on both sides — the page
      //             renders ~40px narrower than the sheet and its
      //             margin:auto centres the gap into view. Computing
      //             the scale ourselves fills the sheet edge to edge.
      //
      // Re-run on resize (orientation flip, sheet snap, pane drag) so
      // the fit stays fresh. Defensive: if the first page isn't
      // measurable yet, fall back to "page-width" — the ResizeObserver
      // fires again once layout settles and recomputes.
      const applyFitScale = () => {
        const state = stateRef.current;
        if (!state?.pagesReady) return;
        try {
          // Desktop: pdfjs "page-width" verbatim (reserves 40px for a
          // real scrollbar; the centred-page-on-dark look is intended).
          const isMobile = window.matchMedia("(max-width: 900px)").matches;
          if (!isMobile) {
            state.viewer.currentScaleValue = "page-width";
            return;
          }
          // Mobile: let pdfjs compute "page-width" FIRST. That value is
          // correct in every respect except it subtracts a hardcoded
          // SCROLLBAR_PADDING (40px) the touch sheet doesn't need.
          // Crucially, pdfjs's scale already bakes in the PDF-point →
          // CSS-pixel factor (96/72); hand-deriving it from a viewport
          // is what overshot by 4/3 before. So: take pdfjs's base
          // scale, measure the page it produced, then scale up by the
          // exact measured ratio to fill the container edge to edge.
          // Self-correcting — no hardcoded padding constant, and the
          // page-width reset each run makes it an idempotent fixpoint.
          state.viewer.currentScaleValue = "page-width";
          const base = state.viewer.currentScale;
          const pageEl = container.querySelector(
            ".pdfViewer .page",
          ) as HTMLElement | null;
          if (!pageEl || !base) return; // ResizeObserver retries
          // getBoundingClientRect forces a synchronous layout, so this
          // reflects the page-width width pdfjs just applied.
          const pageW = pageEl.getBoundingClientRect().width;
          const targetW = container.clientWidth;
          if (pageW > 1 && targetW > 1 && Math.abs(targetW - pageW) > 1) {
            state.viewer.currentScale = base * (targetW / pageW);
          }
        } catch {
          /* viewer mid-teardown */
        }
      };

      // pagesinit fires once the viewer knows the page count and can
      // accept currentPageNumber / find dispatches. Before this fires,
      // navigation calls are silently dropped.
      const onPagesInit = () => {
        if (cancelled) return;
        if (stateRef.current) {
          stateRef.current.pagesReady = true;
        }
        applyFitScale();
        setLoadState("ready");
        // Notify parent of total page count so its chrome can render
        // the page-nav controls. Fires exactly once per document load.
        const total = stateRef.current?.viewer.pagesCount ?? 0;
        if (total > 0) {
          onTotalPagesChangeRef.current?.(total);
        }
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

      // pagechanging fires whenever the viewer's currentPageNumber
      // changes — from user scroll, find-driven scroll, or our own
      // scrollPageIntoView. The payload's pageNumber is 1-based.
      const onPageChanging = (payload: { pageNumber: number }) => {
        if (cancelled) return;
        const cb = onPageChangeRef.current;
        if (cb) cb(payload.pageNumber);
      };

      // D1: pdfjs repaints highlightAll matches every time a page's
      // text layer (re)renders — including pages scrolled into view
      // long after handleFindResults resolved. Re-strip any page that
      // isn't the resolved one so the highlight stays scoped. No-op
      // until a page is resolved (resolvedPageRef null).
      const onTextLayerScoped = (payload: { pageNumber: number }) => {
        if (cancelled) return;
        const st = stateRef.current;
        const resolved = resolvedPageRef.current;
        if (!st || resolved === null || payload.pageNumber === resolved) {
          return;
        }
        clearFindHighlightsOnPage(st.viewer, payload.pageNumber);
      };

      eventBus.on("pagesinit", onPagesInit);
      eventBus.on("updatefindmatchescount", onMatches);
      eventBus.on("pagechanging", onPageChanging);
      eventBus.on("textlayerrendered", onTextLayerScoped);

      // Re-fit pages whenever the container resizes — mobile sheet snap
      // changes (compact/split/full), orientation flips, or desktop pane
      // drags. Without this, the first fit on pagesinit becomes stale
      // and pages either overflow or leave a gap. Coalesced by rAF so a
      // burst of resize events only triggers one rescale.
      let rescaleScheduled = false;
      const rescaleToFit = () => {
        if (rescaleScheduled) return;
        rescaleScheduled = true;
        window.requestAnimationFrame(() => {
          rescaleScheduled = false;
          // applyFitScale picks page-width (desktop) vs explicit
          // numeric (mobile) and has its own pagesReady + teardown
          // guards, so the rAF body just delegates.
          applyFitScale();
        });
      };
      const resizeObserver = new ResizeObserver(rescaleToFit);
      resizeObserver.observe(container);

      stateRef.current = {
        pdfjs,
        viewer,
        eventBus,
        findController,
        pagesReady: false,
        resizeObserver,
      };

      try {
        // Use absolute API_BASE_URL (https://api.helpmateai.xyz in prod,
        // /api in dev) so PDF.js fetches the source directly from the API
        // hostname. The /api/* proxy via Next.js rewrites routes Vercel-
        // edge → Cloudflare in production, which triggers Cloudflare's
        // bot challenge on data-center IPs and breaks the PDF stream.
        // Browser-direct hits to api.helpmateai.xyz pass Cloudflare with
        // the user's residential IP + UA + Bearer token.
        const pdf = await loadPdfDocument(`${API_BASE_URL}/documents/${documentId}/file`);
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
        state.resizeObserver.disconnect();
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

  const bannerNode = renderBanner(banner, errorDetail, pageLabel);

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
});

function renderBanner(
  kind: BannerKind,
  detail: string,
  pageLabel: string,
) {
  if (kind === null) return null;
  const hintPage = parsePageLabel(pageLabel);
  if (kind === "no-match") {
    return (
      <div className="h-pdf-banner h-pdf-banner-soft" role="status">
        Showing Page {hintPage}. We couldn&apos;t pinpoint the exact passage —
        try scrolling a page or two in either direction.
      </div>
    );
  }
  if (kind === "needs-rendition") {
    // Legacy DOCX uploads that don't have a PDF rendition can't be shown
    // inline. We don't offer a download here — the user uploaded the
    // file themselves, so they already have it. The cited passages are
    // still available as text inside the evidence cards.
    return (
      <div className="h-pdf-banner h-pdf-banner-warn" role="alert">
        This document can&apos;t be viewed inline. The cited passages still
        appear as text in the evidence cards.
      </div>
    );
  }
  if (kind === "missing") {
    return (
      <div className="h-pdf-banner h-pdf-banner-warn" role="alert">
        We can&apos;t find this source anymore. The workspace may have expired —
        reload the page to start fresh.
      </div>
    );
  }
  if (kind === "auth") {
    return (
      <div className="h-pdf-banner h-pdf-banner-warn" role="alert">
        Your session timed out. Reload the page to continue reading.
      </div>
    );
  }
  return (
    <div className="h-pdf-banner h-pdf-banner-warn" role="alert">
      The source PDF didn&apos;t load{detail ? ` (${detail})` : ""}.
      Try closing and reopening Read Mode.
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
