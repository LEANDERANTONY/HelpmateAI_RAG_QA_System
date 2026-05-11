"use client";

// Read Mode state — Zustand store with selector-based subscriptions.
//
// Read Mode is a layout transition the workspace enters when a user clicks
// "Open in source" on an evidence card. While in Read Mode:
//   • The chat / answer column collapses to ~45% width (desktop)
//   • A new source-viewer pane occupies the remaining ~55% (min 700px)
//   • The doc strip and evidence rail are hidden
//
// On mobile (<=900px) the source is a draggable bottom sheet (vaul) with
// three snap points: FULL (100%), SPLIT (55%, default), COMPACT (25%, used
// when the soft keyboard is up).
//
// State shape:
//   • mode: 'normal' | 'read'
//   • currentChunk: the source position the viewer should land on
//   • mobileSnap: 'full' | 'split' | 'compact' — bottom-sheet posture
//   • keyboardActive: true when soft keyboard is up (drives snap to COMPACT)
//
// Mobile state machine:
//   FULL  ↔ drag ↔  SPLIT
//                     ↕  (input focus / blur, or visualViewport size delta)
//                  COMPACT
//
// Drag is constrained to FULL↔SPLIT — drag-to-COMPACT is intercepted in
// the MobileSourceSheet and bounces back to SPLIT. COMPACT is reached
// only via keyboard.
//
// Consumer pattern:
//   const mode = useReadModeStatus();
//   const snap = useMobileSnap();
//   const { enterReadMode, setMobileSnap } = useReadModeActions();
//
// Out of scope:
//   • Persistence across reloads — session-local on purpose (spec).
//   • Scroll-position restoration on re-entry (spec defers to "no memory").

import { useShallow } from "zustand/react/shallow";
import { create } from "zustand";

export type ReadModeChunk = {
  chunkId: string;
  pageLabel: string;
  chunkText: string;
  documentId: string;
  fileName: string;
};

export type ReadModeStatus = "normal" | "read";
export type MobileSnap = "full" | "split" | "compact";

type ReadModeState = {
  mode: ReadModeStatus;
  currentChunk: ReadModeChunk | null;
  mobileSnap: MobileSnap;
  keyboardActive: boolean;
  enterReadMode: (chunk: ReadModeChunk) => void;
  exitReadMode: () => void;
  setCurrentChunk: (chunk: ReadModeChunk) => void;
  setMobileSnap: (snap: MobileSnap) => void;
  setKeyboardActive: (active: boolean) => void;
};

export const useReadModeStore = create<ReadModeState>((set) => ({
  mode: "normal",
  currentChunk: null,
  // SPLIT is the default working posture on mobile — chat exposed in the
  // top ~45%, source viewer in the bottom ~55%.
  mobileSnap: "split",
  keyboardActive: false,
  enterReadMode: (chunk) =>
    // Always re-enter at SPLIT so the user doesn't land in COMPACT (which
    // would imply keyboard) or FULL (which hides the chat). Keyboard
    // detection then transitions to COMPACT if needed.
    set({ mode: "read", currentChunk: chunk, mobileSnap: "split", keyboardActive: false }),
  // Clear currentChunk on exit so re-entry never shows stale state — spec
  // says no scroll restoration, so there's nothing worth keeping.
  exitReadMode: () => set({ mode: "normal", currentChunk: null }),
  // setCurrentChunk is the auto-jump path (new answer, citation pill in
  // read mode). No-op when called from normal mode because there's no
  // viewer to scroll — the normal-mode citation path uses the evidence
  // rail flash instead.
  setCurrentChunk: (chunk) =>
    set((state) => (state.mode === "read" ? { currentChunk: chunk } : state)),
  setMobileSnap: (snap) => set({ mobileSnap: snap }),
  setKeyboardActive: (active) =>
    set((state) => {
      if (state.keyboardActive === active) {
        return state;
      }
      // Sync mobileSnap with keyboard state: keyboard up → COMPACT,
      // keyboard down → SPLIT (unless user dragged to FULL, which only
      // happens when keyboard is down anyway).
      if (active) {
        return { keyboardActive: true, mobileSnap: "compact" };
      }
      // Keyboard just dismissed — return to SPLIT regardless of prior
      // FULL/COMPACT. If the user was at FULL before opening the keyboard,
      // they're now back at SPLIT, which matches the spec.
      return { keyboardActive: false, mobileSnap: "split" };
    }),
}));

// Convenience selectors — each subscribes to one slice so re-renders stay
// narrow. EvidenceCards using only `useReadModeActions()` never re-render
// from state changes; SourcePane reading `useCurrentChunk()` re-renders
// only when the chunk changes.

export function useReadModeStatus(): ReadModeStatus {
  return useReadModeStore((state) => state.mode);
}

export function useCurrentChunk(): ReadModeChunk | null {
  return useReadModeStore((state) => state.currentChunk);
}

export function useMobileSnap(): MobileSnap {
  return useReadModeStore((state) => state.mobileSnap);
}

export function useKeyboardActive(): boolean {
  return useReadModeStore((state) => state.keyboardActive);
}

// useShallow keeps the actions object reference-stable across renders. Without
// it, picking multiple actions in one selector would return a new object every
// time the store updates, causing consumers to re-render unnecessarily.
export function useReadModeActions(): Pick<
  ReadModeState,
  "enterReadMode" | "exitReadMode" | "setCurrentChunk" | "setMobileSnap" | "setKeyboardActive"
> {
  return useReadModeStore(
    useShallow((state) => ({
      enterReadMode: state.enterReadMode,
      exitReadMode: state.exitReadMode,
      setCurrentChunk: state.setCurrentChunk,
      setMobileSnap: state.setMobileSnap,
      setKeyboardActive: state.setKeyboardActive,
    })),
  );
}
