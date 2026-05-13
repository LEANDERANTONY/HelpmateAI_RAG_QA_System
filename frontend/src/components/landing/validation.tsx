// Validation strip — spec-sheet §4.3.
//
// Vendor numbers locked in Phase 0 question 2 (real names from the
// README's eval table, not the prototype's anonymized B/C/D). Footnote
// uses the "standard retrieval-and-answer pipeline" wording from the
// spec sheet — softened from validation-strip.md's older "native answer
// modes" copy per Phase 0 pushback 3.5.

const STATS = [
  { num: "0%", label: "False support", muted: false },
  { num: "100%", label: "Abstention when out-of-scope", muted: false },
  { num: "94%", label: "Cited the correct paragraph", muted: true },
];

const VENDORS = [
  { name: "Helpmate", result: "0.0% on a 150-question suite", us: true },
  { name: "OpenAI File Search", result: "6.7% false support", us: false },
  { name: "Vectara", result: "20.0% false support", us: false },
];

export function LandingValidation() {
  return (
    <section className="l-sec" id="validation">
      <div className="l-sec-inner narrow">
        <div className="l-sec-head">
          <div className="eyebrow">
            Validation · 150-question held-out suite
          </div>
          <h2>Zero unsupported answers across the evaluation</h2>
          <p>
            We graded every answer for whether the cited evidence actually
            supports the claim. The numbers below are the full run, not a
            curated sample.
          </p>
        </div>

        <div className="l-val-stats">
          {STATS.map((stat) => (
            <div className="l-val-stat" key={stat.label}>
              <div className={stat.muted ? "num muted" : "num"}>{stat.num}</div>
              <div className="lbl">{stat.label}</div>
            </div>
          ))}
        </div>

        <div className="l-val-rule" aria-hidden />

        <div className="l-val-rows">
          {VENDORS.map((vendor) => (
            <div
              className={vendor.us ? "l-val-row us" : "l-val-row"}
              key={vendor.name}
            >
              <div className="vendor">{vendor.name}</div>
              <div className="result">{vendor.result}</div>
            </div>
          ))}
        </div>

        <div className="l-val-foot">
          Each vendor evaluated using its standard retrieval-and-answer
          pipeline, on the same 150-question held-out suite. Suite, rubric,
          and full run are available on request.
        </div>
      </div>
    </section>
  );
}
