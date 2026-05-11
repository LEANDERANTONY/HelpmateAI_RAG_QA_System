"use client";

import { useEffect, useState } from "react";

// Drive the active claim from the row that currently contains the
// 30vh scan line in the viewport.
//
// Replaces a literal reading of spec-sheet §5.3, which observed
// each row's eyebrow with an IntersectionObserver and rootMargin
// "0px 0px -70% 0px" (fire when the eyebrow enters the top 30% of
// the viewport from below). That worked for claims 1–2 but left
// the LAST claim stuck inactive whenever the section's layout
// ended before its eyebrow could climb into the trigger zone —
// i.e., the page hit maxScroll first, so the trigger event never
// fired. Symmetric edge case at the top: before any eyebrow
// crossed the threshold, the technically-"active" claim was
// whichever default we initialized to.
//
// Row-containment fix: at every animation frame during scroll,
// find the row whose top has scrolled past 30vh. Walking from
// last index toward first and returning the first match gives us
// the "lowest-indexed row that is still in or below the scan
// line". That handles all three regions naturally:
//   • Before the section: no row.top <= scanY → active = 0
//   • Mid-section:        one row contains scanY → that one
//   • Past the section:   all rows are above scanY → last row
//
// Visual behavior matches the spec; only the trigger mechanism
// differs.
//
// On mobile (≤900px) the sticky pin is dropped entirely and every
// visual renders inline below its text, so we skip the listener.
export function useActiveClaim() {
  const [active, setActive] = useState(0);

  useEffect(() => {
    if (window.matchMedia("(max-width: 900px)").matches) {
      return;
    }

    const compute = () => {
      const rows = document.querySelectorAll<HTMLElement>("[data-claim-i]");
      if (!rows.length) return;

      const scanY = window.innerHeight * 0.3;
      let next = 0;
      for (let i = rows.length - 1; i >= 0; i--) {
        if (rows[i].getBoundingClientRect().top <= scanY) {
          next = i;
          break;
        }
      }
      setActive(next);
    };

    let queued = false;
    const onScroll = () => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => {
        compute();
        queued = false;
      });
    };

    compute();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);

  return active;
}
