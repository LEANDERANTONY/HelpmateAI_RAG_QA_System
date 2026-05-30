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
// Mobile state machine — three snap points, all reachable by drag
// (heights: COMPACT 25% < SPLIT 55% < FULL 100%):
//   COMPACT  ↕ drag ↕  SPLIT  ↕ drag ↕  FULL
//
// Transitions:
//   • Drag — vaul resolves the nearest snap on release and whatever it
//     lands on becomes mobileSnap directly, including COMPACT. (Bouncing
//     drag-to-COMPACT back to SPLIT — an earlier design — opened a visible
//     gap between the chat and the sheet mid-animation; see
//     MobileSourceSheet.handleSetActiveSnapPoint.)
//   • Keyboard — soft keyboard up forces COMPACT so the focused chat input
//     stays visible above the sheet; keyboard down returns to SPLIT.
//   • Handle tap / Enter / Space — toggles SPLIT ↔ FULL and returns to
//     SPLIT from COMPACT (never COMPACT → FULL).
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
  // Desktop-only: whether the chat panel is collapsed to its 48px
  // strip-affordance state, giving the PDF center stage. No effect on
  // mobile, where the FULL snap is the equivalent posture.
  chatCollapsed: boolean;
  enterReadMode: (chunk: ReadModeChunk) => void;
  exitReadMode: () => void;
  setCurrentChunk: (chunk: ReadModeChunk) => void;
  setMobileSnap: (snap: MobileSnap) => void;
  setKeyboardActive: (active: boolean) => void;
  setChatCollapsed: (collapsed: boolean) => void;
  toggleChatCollapsed: () => void;
};

export const useReadModeStore = create<ReadModeState>((set) => ({
  mode: "normal",
  currentChunk: null,
  // SPLIT is the default working posture on mobile — chat exposed in the
  // top ~45%, source viewer in the bottom ~55%.
  mobileSnap: "split",
  keyboardActive: false,
  // Desktop chat panel starts expanded; collapse is a deliberate
  // user action.
  chatCollapsed: false,
  enterReadMode: (chunk) =>
    // Always re-enter at SPLIT (mobile) and expanded (desktop) so the
    // user doesn't land in COMPACT, FULL, or collapsed-chat by accident
    // from a previous session. Spec: no posture memory across entries.
    set({
      mode: "read",
      currentChunk: chunk,
      mobileSnap: "split",
      keyboardActive: false,
      chatCollapsed: false,
    }),
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
  setChatCollapsed: (collapsed) => set({ chatCollapsed: collapsed }),
  toggleChatCollapsed: () => set((state) => ({ chatCollapsed: !state.chatCollapsed })),
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

export function useChatCollapsed(): boolean {
  return useReadModeStore((state) => state.chatCollapsed);
}

// useShallow keeps the actions object reference-stable across renders. Without
// it, picking multiple actions in one selector would return a new object every
// time the store updates, causing consumers to re-render unnecessarily.
export function useReadModeActions(): Pick<
  ReadModeState,
  | "enterReadMode"
  | "exitReadMode"
  | "setCurrentChunk"
  | "setMobileSnap"
  | "setKeyboardActive"
  | "setChatCollapsed"
  | "toggleChatCollapsed"
> {
  return useReadModeStore(
    useShallow((state) => ({
      enterReadMode: state.enterReadMode,
      exitReadMode: state.exitReadMode,
      setCurrentChunk: state.setCurrentChunk,
      setMobileSnap: state.setMobileSnap,
      setKeyboardActive: state.setKeyboardActive,
      setChatCollapsed: state.setChatCollapsed,
      toggleChatCollapsed: state.toggleChatCollapsed,
    })),
  );
}
