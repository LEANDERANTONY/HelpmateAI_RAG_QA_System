// Three visuals for the editorial-claims section. Decorative — text
// claims carry the substance — so each visual container is marked
// aria-hidden by the parent.

export function ClaimVis1() {
  return (
    <div className="l-claim1">
      <div className="prose">
        The policyholder must make a nomination{" "}
        <span className="cite-pill ringed">Section 4.2</span> for payment of
        claims in the event of death.
      </div>
      <div className="evi">
        <div className="head">
          <div className="eyebrow">Section 4.2 · p. 26</div>
          <div className="strength">Strong</div>
        </div>
        <div className="preview">
          &ldquo;At the inception of the policy, the policyholder shall make
          a nomination for the payment of claims in the event of death. Any
          change of nomination must be communicated&hellip;&rdquo;
        </div>
      </div>
    </div>
  );
}

export function ClaimVis2() {
  return (
    <div className="l-pip-stack">
      <div className="l-pip-row supported">
        <span className="dot" aria-hidden />
        <span className="lbl">Supported · cited evidence</span>
      </div>
      <div className="l-pip-row partial">
        <span className="dot" aria-hidden />
        <span className="lbl">Partial · supports A, not B</span>
      </div>
      <div className="l-pip-row abstained">
        <span className="dot" aria-hidden />
        <span className="lbl">Abstained · source is silent</span>
      </div>
    </div>
  );
}

export function ClaimVis3() {
  return (
    <div className="l-claim3">
      <div className="link">
        <span aria-hidden>↗</span>
        <span>Open in source · </span>
        <span className="file">PolicyWording.pdf</span>
      </div>
      <div className="pages">
        <span>P. 1</span>
        <span className="stripe" aria-hidden />
        <span>P. 30</span>
      </div>
    </div>
  );
}
