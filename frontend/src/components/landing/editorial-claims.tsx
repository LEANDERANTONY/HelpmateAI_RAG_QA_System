"use client";

import {
  ClaimVis1,
  ClaimVis2,
  ClaimVis3,
} from "@/components/landing/claim-visuals";
import { useActiveClaim } from "@/components/landing/use-active-claim";

// Sticky-pinned editorial claims — spec-sheet §4.4 + §5.3.
//
// Numerals are 01 / 02 / 03 in mono caps (Phase 0 pushback 3.4 — no
// unicode ①②③ because Space Grotesk has spotty fallback for those).
// The fourth current-landing claim ("Handles exact and broad")
// folded into claim 03 per editorial-claims.md.

const CLAIMS = [
  {
    n: "01",
    tag: "Shows the source",
    head: "Every claim carries the passage it came from.",
    body: "Click a citation pill and the evidence column jumps to the exact paragraph — verbatim, with the page and section it came from. Click 'Open in source' to see the same passage in the actual PDF, highlighted on its page.",
    Vis: ClaimVis1,
  },
  {
    n: "02",
    tag: "Knows when to abstain",
    head: "Three honest kinds of answer, not just one.",
    body: "Answers come in three flavors: supported when the source covers it, partial when it covers some of it, abstained when it doesn't cover it at all. We grade for whether the evidence actually supports the claim, not how confident the sentence sounds.",
    Vis: ClaimVis2,
  },
  {
    n: "03",
    tag: "Stays inside the document",
    head: "It will not paraphrase the internet at you.",
    body: "Helpmate reads only what you've uploaded. No outside knowledge slipped in, no “as a general rule” answers, no quietly-helpful guesses. If the document doesn't say it, neither does Helpmate.",
    Vis: ClaimVis3,
  },
] as const;

export function LandingClaims() {
  const active = useActiveClaim();

  return (
    <section className="l-sec" id="features">
      <div className="l-sec-inner">
        <div className="l-sec-head l-claims-head">
          <div className="eyebrow">What you get</div>
          <h2>Three behaviors most assistants skip.</h2>
          <p>
            Helpmate answers your questions about a document, but the way it
            answers is what matters. You&rsquo;ll see all three behaviors in
            your first response.
          </p>
        </div>

        <div className="l-claim-grid">
          <div className="l-claims-rail">
            {CLAIMS.map((c, i) => (
              <article
                key={c.n}
                className={
                  i === active ? "l-claim-row is-active" : "l-claim-row"
                }
                data-claim-i={i}
              >
                <div className="l-claim-text">
                  <div className="eyebrow">
                    <span className="num">{c.n}</span>
                    {c.tag}
                  </div>
                  <h3>{c.head}</h3>
                  <p>{c.body}</p>
                </div>
                <div className="l-claim-vis-inline" aria-hidden>
                  <c.Vis />
                </div>
              </article>
            ))}
          </div>

          <aside className="l-claim-sticky" aria-hidden>
            <div className="l-claim-stack">
              {CLAIMS.map((c, i) => (
                <div
                  key={c.n}
                  className={
                    i === active
                      ? "l-claim-frame is-active"
                      : "l-claim-frame"
                  }
                >
                  <c.Vis />
                </div>
              ))}
              <div className="l-claim-progress">
                {CLAIMS.map((c, i) => (
                  <span
                    key={c.n}
                    className={
                      i === active ? "l-claim-tick is-active" : "l-claim-tick"
                    }
                  />
                ))}
              </div>
            </div>
          </aside>
        </div>
      </div>
    </section>
  );
}
