// Final CTA — spec-sheet §4.6 with Phase 5 locked overrides.
// Indexing-state mockup (workspace-indexing.png) sits as the section
// background; its phase timeline + skeleton evidence cards have
// effectively no readable text, so the brightness/saturation filter
// doesn't fight the CTA prose.

const WORKSPACE_URL = "https://app.helpmateai.xyz";
const FINAL_BG = "/landing/workspace-indexing.png";

export function LandingFinalCTA() {
  return (
    <section className="l-final" aria-labelledby="final-cta-heading">
      <div
        className="l-final-bg"
        aria-hidden
        style={{ backgroundImage: `url(${FINAL_BG})` }}
      />
      <div className="l-final-wash" aria-hidden />
      <div className="l-final-inner">
        <h2 id="final-cta-heading">Ready when you are</h2>
        <p>
          Upload a document. Ask one question. See if it earns the answer.
        </p>
        <a className="btn" href={WORKSPACE_URL}>
          Open workspace
          <span aria-hidden>→</span>
        </a>
      </div>
    </section>
  );
}
