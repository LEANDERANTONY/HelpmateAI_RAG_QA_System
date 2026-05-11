const WORKSPACE_URL = "https://app.helpmateai.xyz";

export function LandingHero() {
  return (
    <section className="l-hero" id="hero">
      <div className="l-hero-inner">
        <h1>Helpmate AI</h1>
        <p>
          Helpmate reads the policy, the contract, the manual and answers
          with citations to the exact paragraph it relied on. When the source
          is silent, it abstains.
        </p>
        <a className="l-hero-cta" href={WORKSPACE_URL}>
          Open workspace
          <span className="arrow" aria-hidden>
            →
          </span>
        </a>
      </div>
    </section>
  );
}
