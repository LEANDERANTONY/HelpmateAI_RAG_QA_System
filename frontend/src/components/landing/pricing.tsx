// Pricing section — three tiers, middle "Pro" tier visually anchored
// with the accent-filled treatment (matches the reference shot).
//
// Free + Business cards: dark canvas, mint border + soft outer glow,
//                        mint-filled CTA. The interior stays neutral
//                        dark — only the border-line and the halo
//                        carry the accent, so the focal Pro card
//                        doesn't have to fight any internal tint.
// Pro card:              solid mint gradient fill, dark "punch-through"
//                        anchors (the MOST POPULAR pill at the top and
//                        the Get Pro button) both use --bg-page so they
//                        read as windows cut into the mint card.
//
// CTAs across the row form an alternating contrast rhythm — green button
// on dark card, dark button on green card, green button on dark card.
// The middle one inverts the relationship without losing the focal lock.

const WORKSPACE_URL = "https://app.helpmateai.xyz";

type Tier = {
  id: "free" | "pro" | "business";
  name: string;
  price: number;
  blurb: string;
  cta: { label: string; href: string };
  features: string[];
  featured?: boolean;
};

// Tier caps + prices are sized against actual unit economics — see the
// COGS analysis in the project README/docs. The short version:
//
//   • Storage is essentially free (~$0.02/GB-month) so "Unlimited
//     documents" is safe at all paid tiers.
//   • The LLM answer model is the dominant cost driver. We use
//     gpt-5.4-mini as the default workhorse (~$0.008/query) and quota
//     GPT-5.5 as Premium answers (~$0.052/query, 6.5x more expensive).
//   • Question caps + Premium-answer caps keep worst-case per-user
//     COGS comfortably under the price floor:
//        Pro      $9 revenue   /  ~$5.30/mo worst-case cost   = 41% margin
//        Business $39 / seat   /  ~$21/seat worst-case cost   = 46% margin
//
// If we ever raise the answer model to gpt-5.5 by default, the math
// no longer works at these prices — re-run before promising more.
const TIERS: Tier[] = [
  {
    id: "free",
    name: "Free",
    price: 0,
    blurb: "Try Helpmate on a few documents.",
    cta: { label: "Start free", href: WORKSPACE_URL },
    features: [
      "3 active documents",
      "25 MB file size cap",
      "50 questions / month",
      "Read Mode + citations",
      "30-day history retention",
    ],
  },
  {
    id: "pro",
    name: "Pro",
    price: 9,
    blurb: "Unlimited docs for individuals.",
    cta: { label: "Get Pro", href: WORKSPACE_URL },
    featured: true,
    features: [
      "Unlimited documents",
      "150 MB file size cap",
      "500 questions / month",
      "25 Premium answers (GPT-5.5) / month",
      "1-year history retention",
      "Export to Word + Notion",
    ],
  },
  {
    id: "business",
    name: "Business",
    price: 39,
    blurb: "Teams with admin + SSO. Billed per seat.",
    cta: { label: "Contact us", href: "mailto:hello@helpmateai.xyz" },
    features: [
      "Everything in Pro",
      "500 MB file size cap",
      "2,000 questions / seat",
      "100 Premium answers / seat",
      "Unlimited history retention",
      "5-seat team workspace",
      "SSO + audit log",
      "Priority support",
    ],
  },
];

function Check() {
  // Inline SVG so we don't import an icon set just for one section. Path
  // matches the body-text stroke weight in the rest of the landing.
  return (
    <svg
      aria-hidden="true"
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function PricingCard({ tier }: { tier: Tier }) {
  const isFeatured = Boolean(tier.featured);
  return (
    <article
      className={isFeatured ? "l-pricing-card is-featured" : "l-pricing-card"}
    >
      {isFeatured ? (
        <span className="l-pricing-badge" aria-hidden>
          Most popular
        </span>
      ) : null}
      <header className="l-pricing-card-head">
        <p className="l-pricing-name">{tier.name}</p>
        <p className="l-pricing-blurb">{tier.blurb}</p>
      </header>
      <p className="l-pricing-price">
        <span className="num">${tier.price}</span>
        <span className="per">/month</span>
      </p>
      <a className="l-pricing-cta" href={tier.cta.href}>
        {tier.cta.label}
      </a>
      <ul className="l-pricing-features">
        {tier.features.map((feature) => (
          <li key={feature}>
            <Check />
            <span>{feature}</span>
          </li>
        ))}
      </ul>
    </article>
  );
}

export function LandingPricing() {
  return (
    <section className="l-sec l-pricing-sec" id="pricing">
      <div className="l-sec-inner wide">
        <div className="l-sec-head">
          <div className="eyebrow">Pricing</div>
          <h2>Start free, upgrade when you need more</h2>
          <p>
            Same grounded answers, more headroom. Pick the tier that matches
            how much you read.
          </p>
        </div>
        <div className="l-pricing-grid">
          {TIERS.map((tier) => (
            <PricingCard key={tier.id} tier={tier} />
          ))}
        </div>
      </div>
    </section>
  );
}
