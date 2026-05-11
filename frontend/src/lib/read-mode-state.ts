"use client";

// Read Mode state — Zustand store with selector-based subscriptions.
//
// Read Mode is a layout transition the workspace enters when a user clicks
// "Open in source" on an evidence card. While in Read Mode:
//   • The chat / answer column collapses to ~45% width
//   • A new source-viewer pane occupies the remaining ~55% (min 700px)
//   • The doc strip and evidence rail are hidden
//   • The doc name relocates to the topbar
//
// On mobile (<=900px) Read Mode becomes a full-screen overlay so the source
// fills the viewport — there's no two-pane to coexist with.
//
// State shape:
//   • mode: 'normal' | 'read'
//   • currentChunk: the source position the viewer should land on
//
// Why Zustand and not context+reducer?
// Stage 3b/3c bolts more consumers onto this slice (SourcePane, citation
// pills nested in every answer, Topbar, EvidenceCards). Context fans out
// re-renders to every consumer on every state change; Zustand's selectors
// let each consumer subscribe to just the slice it needs. EvidenceCards
// using `useReadModeActions()` never re-render from state changes at all.
//
// Consumer pattern:
//   const mode = useReadModeStatus();                   // re-renders on mode flips
//   const chunk = useCurrentChunk();                    // re-renders on chunk changes
//   const { enterReadMode } = useReadModeActions();     // never re-renders from state
//
// Or use the lower-level selector form for ad-hoc slices:
//   const fileName = useReadModeStore(s => s.currentChunk?.fileName);
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
  // Useful for the viewer chrome / topbar slot:
  documentId: string;
  fileName: string;
};

export type ReadModeStatus = "normal" | "read";

type ReadModeState = {
  mode: ReadModeStatus;
  currentChunk: ReadModeChunk | null;
  enterReadMode: (chunk: ReadModeChunk) => void;
  exitReadMode: () => void;
  setCurrentChunk: (chunk: ReadModeChunk) => void;
};

export const useReadModeStore = create<ReadModeState>((set) => ({
  mode: "normal",
  currentChunk: null,
  enterReadMode: (chunk) => set({ mode: "read", currentChunk: chunk }),
  // Clear currentChunk on exit so re-entry never shows stale state — spec
  // says no scroll restoration, so there's nothing worth keeping.
  exitReadMode: () => set({ mode: "normal", currentChunk: null }),
  // setCurrentChunk is the auto-jump path (new answer, citation pill in
  // read mode). It's a no-op when called from normal mode because there's
  // no viewer to scroll — the normal-mode citation path uses the evidence
  // rail flash instead.
  setCurrentChunk: (chunk) =>
    set((state) => (state.mode === "read" ? { currentChunk: chunk } : state)),
}));

// Convenience selectors. These are the recommended consumption points
// because each subscribes to exactly one slice — re-renders stay narrow.

export function useReadModeStatus(): ReadModeStatus {
  return useReadModeStore((state) => state.mode);
}

export function useCurrentChunk(): ReadModeChunk | null {
  return useReadModeStore((state) => state.currentChunk);
}

// useShallow keeps the actions object reference-stable across renders. Without
// it, picking three actions in one selector would return a new object every
// time the store updates, causing consumers to re-render unnecessarily.
export function useReadModeActions(): Pick<
  ReadModeState,
  "enterReadMode" | "exitReadMode" | "setCurrentChunk"
> {
  return useReadModeStore(
    useShallow((state) => ({
      enterReadMode: state.enterReadMode,
      exitReadMode: state.exitReadMode,
      setCurrentChunk: state.setCurrentChunk,
    })),
  );
}
