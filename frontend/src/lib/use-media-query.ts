"use client";

// SSR-safe media query hook built on useSyncExternalStore (React 18+).
//
// useSyncExternalStore is the canonical pattern for subscribing to
// browser-native event sources like matchMedia. It avoids the
// useState-inside-useEffect anti-pattern (cascading renders, missed
// updates between mount and effect tick) and gives us a clean SSR
// snapshot via the third arg.
//
// During SSR / before the first browser tick we return `null` instead
// of `false`, so callers can branch on "unknown yet" rather than
// committing to the wrong viewport. SourcePaneMount uses this signal
// to render nothing for one tick rather than flashing the desktop
// pane on mobile.

import { useCallback, useSyncExternalStore } from "react";

export function useMediaQuery(query: string): boolean | null {
  // Memo the subscribe function so useSyncExternalStore doesn't churn
  // its subscription on every render. The function captures `query`
  // by closure; if `query` changes, useCallback returns a new function
  // and useSyncExternalStore re-subscribes — correct behavior.
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (typeof window === "undefined" || typeof window.matchMedia === "undefined") {
        return () => {};
      }
      const mql = window.matchMedia(query);
      if (typeof mql.addEventListener === "function") {
        mql.addEventListener("change", onChange);
        return () => mql.removeEventListener("change", onChange);
      }
      // Safari < 14 fallback. Keeps older mobile Safari users working
      // without crashing on addEventListener-not-a-function.
      mql.addListener(onChange);
      return () => mql.removeListener(onChange);
    },
    [query],
  );

  const getSnapshot = useCallback(() => {
    if (typeof window === "undefined" || typeof window.matchMedia === "undefined") {
      return null;
    }
    return window.matchMedia(query).matches;
  }, [query]);

  // Server snapshot is `null` so SSR HTML doesn't commit to a viewport
  // guess. The hydration tick reads the real value via getSnapshot.
  const getServerSnapshot = useCallback((): boolean | null => null, []);

  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
