"use client";

// Read Mode state — context + reducer (NOT Zustand, just to stay
// consistent with the rest of the workspace's React-only patterns).
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
// The "current chunk" is the source position the viewer should land on. It's
// updated by:
//   • enterReadMode(chunk) — initial entry from an evidence card
//   • setCurrentChunk(chunk) — auto-jump when a new answer arrives, or when
//     a citation pill is clicked while already in Read Mode
//
// Out of scope for Stage 3a:
//   • The PDF.js viewer itself (Stage 3b)
//   • Auto-jump wiring (Stage 3c)
//   • Persistence across reloads — session-local only on purpose

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useReducer,
  type ReactNode,
} from "react";
import { createElement } from "react";

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
};

type ReadModeAction =
  | { type: "enter"; chunk: ReadModeChunk }
  | { type: "exit" }
  | { type: "set-chunk"; chunk: ReadModeChunk };

const INITIAL_STATE: ReadModeState = {
  mode: "normal",
  currentChunk: null,
};

function readModeReducer(state: ReadModeState, action: ReadModeAction): ReadModeState {
  switch (action.type) {
    case "enter":
      return { mode: "read", currentChunk: action.chunk };
    case "exit":
      // Keep currentChunk null on exit so re-entry doesn't accidentally show
      // stale state — Stage 3a spec says no scroll restoration anyway.
      return { mode: "normal", currentChunk: null };
    case "set-chunk":
      // Only mutate currentChunk when we're already in read mode. If we're
      // in normal mode, setCurrentChunk is a no-op (the citation-pill path
      // routes through the normal-mode flash behavior instead).
      if (state.mode !== "read") {
        return state;
      }
      return { ...state, currentChunk: action.chunk };
    default:
      return state;
  }
}

type ReadModeContextValue = ReadModeState & {
  enterReadMode: (chunk: ReadModeChunk) => void;
  exitReadMode: () => void;
  setCurrentChunk: (chunk: ReadModeChunk) => void;
};

const ReadModeContext = createContext<ReadModeContextValue | null>(null);

export function ReadModeProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(readModeReducer, INITIAL_STATE);

  // Stable identities so consumers' useEffect deps don't churn.
  const enterReadMode = useCallback((chunk: ReadModeChunk) => {
    dispatch({ type: "enter", chunk });
  }, []);
  const exitReadMode = useCallback(() => {
    dispatch({ type: "exit" });
  }, []);
  const setCurrentChunk = useCallback((chunk: ReadModeChunk) => {
    dispatch({ type: "set-chunk", chunk });
  }, []);

  const value = useMemo<ReadModeContextValue>(
    () => ({
      mode: state.mode,
      currentChunk: state.currentChunk,
      enterReadMode,
      exitReadMode,
      setCurrentChunk,
    }),
    [state.mode, state.currentChunk, enterReadMode, exitReadMode, setCurrentChunk],
  );

  return createElement(ReadModeContext.Provider, { value }, children);
}

export function useReadMode(): ReadModeContextValue {
  const value = useContext(ReadModeContext);
  if (value === null) {
    throw new Error("useReadMode must be used within a ReadModeProvider");
  }
  return value;
}
