"use client";

// Mobile source viewer — vaul-based draggable bottom sheet with three
// snap points (FULL/SPLIT/COMPACT). Controlled by the Read Mode store
// so the snap state machine survives across re-renders.
//
// Why vaul vs build-from-scratch:
//   • vaul handles drag physics, snap-point resolution, and reduced-motion
//     fallback — three things that take ~200 LOC each to get right.
//   • Bundle cost is ~12KB gzipped — acceptable for the UX we need.
//
// The drag-to-COMPACT interception:
//   COMPACT is reserved for keyboard-up. User drag should bounce between
//   FULL and SPLIT only. Vaul's snap-point array includes COMPACT (so we
//   can programmatically snap there when keyboard appears) but our
//   setActiveSnapPoint handler bounces user-initiated drags away from
//   COMPACT back to SPLIT.
//
// modal={false} keeps the chat behind the sheet interactive — user can
// scroll Q&A history and tap the input while the sheet is at SPLIT or
// COMPACT. This is the whole point of the three-snap design.
//
// dismissible={false} prevents drag-to-dismiss. The X button is the only
// way to exit Read Mode on mobile, per spec.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Drawer } from "vaul";

import { PageNav } from "@/components/viewer/page-nav";
import { PdfViewer, type PdfViewerHandle } from "@/components/viewer/pdf-viewer";
import { API_BASE_URL } from "@/lib/api";
import {
  useCurrentChunk,
  useKeyboardActive,
  useMobileSnap,
  useReadModeActions,
  type MobileSnap,
} from "@/lib/read-mode-state";
import { parsePageLabel } from "@/lib/search-anchor";

// Numeric snap fractions vaul understands. Order matters — vaul resolves
// the nearest snap on release based on the array index, so [0.25, 0.55, 1]
// gives us COMPACT → SPLIT → FULL in increasing height. Kept in sync with
// the MobileSnap string literals via the two helpers below.
const SNAP_POINTS: ReadonlyArray<number> = [0.25, 0.55, 1] as const;

function snapToFraction(snap: MobileSnap): number {
  if (snap === "full") return 1;
  if (snap === "split") return 0.55;
  return 0.25;
}

function fractionToSnap(value: number | string | null): MobileSnap | null {
  // Vaul passes numbers when snapPoints is numeric. The string branch
  // is defensive — if vaul ever returns a string (e.g. "55%"), we'd
  // ignore it and not crash.
  if (typeof value !== "number") return null;
  if (value === 1) return "full";
  if (value === 0.55) return "split";
  if (value === 0.25) return "compact";
  return null;
}

export function MobileSourceSheet() {
  const currentChunk = useCurrentChunk();
  const mobileSnap = useMobileSnap();
  const keyboardActive = useKeyboardActive();
  const { exitReadMode, setMobileSnap } = useReadModeActions();

  // Live page tracking for the page-pill, same chunkId-keyed pattern as
  // SourcePane so a chunk switch naturally resets without an effect.
  const [pageState, setPageState] = useState<{ chunkId: string; page: number } | null>(null);
  const visiblePage =
    pageState && pageState.chunkId === currentChunk?.chunkId ? pageState.page : null;
  const handlePageChange = (page: number) => {
    if (!currentChunk) return;
    setPageState({ chunkId: currentChunk.chunkId, page });
  };

  // Total page count from pdfjs (held in state so PageNav re-renders
  // when pdfjs finishes parsing).
  const [totalPages, setTotalPages] = useState(0);
  const handleTotalPagesChange = useCallback((count: number) => {
    setTotalPages(count);
  }, []);

  // Imperative ref into the viewer for page nav.
  const viewerRef = useRef<PdfViewerHandle | null>(null);
  const handleJumpToPage = useCallback((pageNumber: number) => {
    viewerRef.current?.scrollToPage(pageNumber);
  }, []);

  // Focus the close button on mount for keyboard users. On mobile this
  // mostly affects external-keyboard users — soft keyboards don't have
  // a meaningful focus ring on the button — but it keeps the a11y
  // behavior consistent with the desktop pane.
  const closeBtnRef = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    const t = window.setTimeout(() => closeBtnRef.current?.focus(), 50);
    return () => window.clearTimeout(t);
  }, []);

  // Measure the rendering offset between the sheet's bounding box and
  // where its first child actually paints. On most devices this is 0
  // (children render where CSS says they should). On Chrome's iPhone
  // DevTools emulator — and possibly some real-device combinations of
  // `position: fixed; bottom: 0; height: 100dvh` + vaul's transform —
  // children render ~30+px ABOVE the sheet's reported top, pushing the
  // drag handle off-screen at FULL.
  //
  // Rather than hardcoding the compensation, we insert a 1px sentinel
  // as the sheet's first child, measure its real position, and store
  // the magnitude as a CSS variable. Hardware that doesn't have the
  // quirk reports 0 and pays nothing; hardware that does gets exact
  // compensation. Re-runs on viewport resize / orientation change.
  useEffect(() => {
    // Drawer.Content portals into <body> after the component mounts;
    // the element may not be in the DOM yet on the first useEffect
    // run. rAF + querySelector picks it up reliably without coupling
    // to vaul's ref-forwarding quirks. The portaled sheet is unique
    // by class so a single selector finds it.
    const measure = () => {
      const sheet = document.querySelector(".h-mobile-sheet");
      if (!(sheet instanceof HTMLElement)) return;
      const sentinel = document.createElement("div");
      sentinel.style.cssText =
        "position:relative;width:0;height:1px;margin:0;padding:0;visibility:hidden;pointer-events:none";
      sheet.insertBefore(sentinel, sheet.firstChild);
      const sheetTop = sheet.getBoundingClientRect().top;
      const sentinelTop = sentinel.getBoundingClientRect().top;
      sentinel.remove();
      // Negative offset means the sentinel (and any flex-flow children
      // following it) render above the sheet's reported top — exactly
      // the quirk we want to counter. Clamp to 0 so we never apply
      // negative padding when the quirk is absent.
      const offset = Math.max(0, sheetTop - sentinelTop);
      // Set on documentElement (not the sheet itself) because vaul
      // rewrites the sheet's inline style on every snap/drag update,
      // wiping any custom properties we set there. The :root inherits
      // down to the portaled sheet via the normal CSS cascade.
      document.documentElement.style.setProperty(
        "--quirk-offset",
        `${offset}px`,
      );
    };

    // First measurement on the next frame so the portal has flushed.
    const raf = window.requestAnimationFrame(measure);
    // Orientation flips and dvh changes (Safari URL bar) can shift the
    // quirk magnitude. Re-measure on resize.
    const onResize = () => measure();
    window.addEventListener("resize", onResize);
    return () => {
      window.cancelAnimationFrame(raf);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  // Derive vaul's controlled snap value from our string snap. Memo-ing so
  // vaul's deps don't churn on unrelated re-renders.
  const activeSnapPoint = useMemo(() => snapToFraction(mobileSnap), [mobileSnap]);

  const handleSetActiveSnapPoint = useCallback(
    (value: number | string | null) => {
      const next = fractionToSnap(value);
      if (next === null) {
        return;
      }
      // Drag-to-COMPACT guard: COMPACT is reserved for keyboard-driven
      // transitions. If the user manually drags down to COMPACT and the
      // keyboard isn't up, snap them back to SPLIT. We defer the
      // bounce-back to the next tick — without the defer, vaul ignores
      // the prop change because it just committed its internal
      // activeSnapPointIndex to compact in the same event-loop tick.
      // The result of the synchronous-bounce path is a wedged state:
      // React thinks we're at split (so CSS expands the conv), vaul
      // thinks we're at compact (so the sheet stays low), and the user
      // sees a fat dead-zone between them. The timeout punts the prop
      // change to the next microtask cycle so vaul has finished
      // settling and will respond to the new activeSnapPoint by
      // animating back up.
      if (next === "compact" && !keyboardActive) {
        window.setTimeout(() => setMobileSnap("split"), 0);
        return;
      }
      setMobileSnap(next);
    },
    [keyboardActive, setMobileSnap],
  );

  // Defensive — shouldn't render with no chunk, but the store type allows it.
  if (!currentChunk) {
    return null;
  }

  return (
    <Drawer.Root
      open
      // onOpenChange runs when vaul thinks the drawer should close. Because
      // dismissible={false} the only way this fires is programmatic, so we
      // route it to exitReadMode for safety. The X button calls exitReadMode
      // directly; this is the belt-and-suspenders path.
      onOpenChange={(open) => {
        if (!open) exitReadMode();
      }}
      snapPoints={SNAP_POINTS as number[]}
      activeSnapPoint={activeSnapPoint}
      setActiveSnapPoint={handleSetActiveSnapPoint}
      dismissible={false}
      modal={false}
    >
      <Drawer.Portal>
        {/* No <Drawer.Overlay> — modal={false} means we don't want a
            backdrop that blocks the chat. The chat behind the sheet is
            meant to stay interactive at SPLIT and COMPACT. */}
        <Drawer.Content
          className="h-mobile-sheet"
          data-mobile-snap={mobileSnap}
          aria-label="Source viewer"
        >
          {/* Vaul (via radix Dialog under the hood) emits a console
              warning when Drawer.Content has neither a Title nor a
              Description. Both are screen-reader-only here because
              the visible chrome already conveys the same info via
              the page-pill and close button. */}
          <Drawer.Title className="h-sr-only">Source viewer</Drawer.Title>
          <Drawer.Description className="h-sr-only">
            Drag the handle to resize. Use the close button to exit Read Mode.
          </Drawer.Description>

          {/* Drag handle — pill centered at the top, with a generous hit
              target via padding. vaul recognises the handle by data
              attribute and only initiates drag from this region (rather
              than the whole sheet body, which would interfere with
              PDF.js's own scroll/touch).
              We also wrap it in a role="button" with onClick to toggle
              FULL↔SPLIT. Users on a phone often discover this affordance
              by tap before they discover the drag, and from the FULL
              posture the drag-down gesture is awkward (the handle is
              right under the status bar) — tapping is the escape hatch.
              The click handler is suppressed if the user actually
              dragged (vaul fires its own pointer pipeline; a real tap
              ends with a click event, a drag does not). */}
          <div
            className="h-mobile-sheet-handle-zone"
            data-vaul-drag-handle
            role="button"
            tabIndex={0}
            aria-label={
              mobileSnap === "full"
                ? "Drag or tap to reduce sheet height"
                : "Drag or tap to expand sheet height"
            }
            onClick={() => {
              // Toggle FULL ↔ SPLIT. Ignore COMPACT — that's a
              // keyboard-driven posture; tapping the handle while
              // COMPACT should return to SPLIT (most useful for the
              // user) rather than jump to FULL.
              setMobileSnap(mobileSnap === "full" ? "split" : "full");
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                setMobileSnap(mobileSnap === "full" ? "split" : "full");
              }
            }}
          >
            <div className="h-mobile-sheet-handle" aria-hidden />
          </div>

          {/* Chrome — close button only on mobile sheet (no filename
              meta block here; chat behind the sheet shows the doc strip
              context). */}
          <header className="h-mobile-sheet-chrome">
            <PageNav
              currentPage={visiblePage ?? parsePageLabel(currentChunk.pageLabel)}
              totalPages={totalPages}
              onJump={handleJumpToPage}
            />
            <div className="h-mobile-sheet-chrome-spacer" aria-hidden />
            <button
              ref={closeBtnRef}
              type="button"
              className="h-source-close h-mobile-sheet-close"
              onClick={exitReadMode}
              aria-label="Exit Read Mode"
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
              onDownloadOriginal={() => {
                // NOTE: window.open can't attach a Bearer token, so this
                // will 401 in production. Follow-up: switch to fetch +
                // Blob + anchor[download]. URL uses API_BASE_URL so it
                // goes browser-direct in prod (avoids the Cloudflare
                // bot-challenge that the /api/* proxy route triggers).
                window.open(
                  `${API_BASE_URL}/documents/${currentChunk.documentId}/file?download=1`,
                  "_blank",
                );
              }}
            />
          </div>
        </Drawer.Content>
      </Drawer.Portal>
    </Drawer.Root>
  );
}

function CloseGlyph() {
  return (
    <svg aria-hidden="true" height="16" viewBox="0 0 24 24" width="16">
      <path d="M6 6l12 12M18 6l-12 12" />
    </svg>
  );
}
