"use client";

import { useState } from "react";

// Flow carousel — spec-sheet §4.5. Minimal token swap of the existing
// 4-step section (Phase 0 question 3 default). Tab content preserved
// from the prototype's FLOW_STEPS array verbatim, including which
// mockup goes with which step. Auto-advance default OFF — implementation
// could gate it behind a prop later, but shipped default is OFF (per
// spec: auto-advancing tabs fight Cmd+F).

const FLOW_STEPS = [
  {
    n: "01",
    tab: "Upload",
    head: "Drop a document, get its outline",
    body: "PDF, contract, policy, manual. Helpmate reads through it, builds a section-by-section map, and shows you the outline before you ask anything. Your document stays in your workspace — we don't shuttle it off to train anything.",
    img: "/landing/workspace-first-time.png",
  },
  {
    n: "02",
    tab: "Understand",
    head: "Ask in your own words",
    body: 'No template prompts, no "act as a lawyer" framing. Helpmate finds the paragraphs that actually answer your question, then writes from those — not from what it half-remembers.',
    img: "/landing/workspace-indexing.png",
  },
  {
    n: "03",
    tab: "Find evidence",
    head: "Three passages, ranked",
    body: "You see the evidence the answer relied on, ranked by support strength: strong, weak, or partial. Each card is the verbatim paragraph with the section and page it came from.",
    img: "/landing/workspace-flagship.png",
  },
  {
    n: "04",
    tab: "Cite answer",
    head: "Citations, not citations-shaped marketing",
    body: "Every claim in the answer carries an inline pill. Click it to jump the evidence column to the cited paragraph. Click 'Open in source' to see the document itself beside the answer, scrolled to that page with the passage highlighted.",
    img: "/landing/workspace-readmode.png",
  },
] as const;

export function LandingFlow() {
  const [active, setActive] = useState(0);

  return (
    <section className="l-sec" id="how-it-works">
      <div className="l-sec-inner wide">
        <div className="l-sec-head">
          <div className="eyebrow">How it works</div>
          <h2>From upload to cited answer in four steps</h2>
        </div>

        <div className="l-flow-tabs" role="tablist" aria-label="Flow steps">
          {FLOW_STEPS.map((step, i) => (
            <button
              key={step.n}
              role="tab"
              type="button"
              aria-selected={i === active}
              aria-controls={`flow-step-${i}`}
              className={i === active ? "l-flow-tab active" : "l-flow-tab"}
              onClick={() => setActive(i)}
            >
              <span className="num">{step.n}</span>
              <span>{step.tab}</span>
            </button>
          ))}
        </div>

        <div className="l-flow-active">
          {FLOW_STEPS.map((step, i) => (
            <div
              key={step.n}
              id={`flow-step-${i}`}
              role="tabpanel"
              aria-hidden={i !== active}
              className={
                i === active ? "l-flow-step is-active" : "l-flow-step"
              }
            >
              <div
                className="l-flow-mockup"
                role="img"
                aria-label={`${step.tab} workspace screenshot`}
                style={{ backgroundImage: `url(${step.img})` }}
              />
              <div className="l-flow-claim">
                <div className="step">
                  <span>Step {step.n}</span>
                  <span aria-hidden>·</span>
                  <span>{step.tab}</span>
                </div>
                <h3>{step.head}</h3>
                <p>{step.body}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
