"use client";

// Pricing section — three tiers, middle "Pro" tier visually anchored
// with the accent-filled treatment (matches the reference shot).
//
// Marked "use client" so we can read the signed-in Supabase user
// inside the CTAs and bind the Lemon Squeezy checkout URL with that
// user's id (via ?checkout[custom][user_id]=). Without that binding
// the LS webhook can't reconcile the subscription back to the
// account, so a signed-out customer would silently get the wrong
// tier after payment. The pricing copy is tiny, so the cost of
// client-rendering is negligible.

import { useEffect, useState } from "react";

import { getCheckoutUrl, isLemonSqueezyEnabled } from "@/lib/api";
import { capturePostHogEvent } from "@/components/posthog-provider";
import { createClient } from "@/lib/supabase/client";
import { isSupabaseConfigured } from "@/lib/supabase/config";

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
//   • The product is a single-document workspace — one active doc per
//     user; a new upload supersedes the prior. There is no per-user
//     document library to bound, so storage is a non-factor and
//     document COUNT is deliberately NOT a tier lever (don't re-add
//     "N documents" / "Unlimited documents" copy — it misrepresents
//     the model). Tiers differentiate on file size, questions/mo,
//     Premium-answer caps, and retention.
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
    blurb: "Try Helpmate on your own document.",
    cta: { label: "Start free", href: WORKSPACE_URL },
    features: [
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
    blurb: "More questions, bigger files, premium answers.",
    cta: { label: "Get Pro", href: WORKSPACE_URL },
    featured: true,
    features: [
      "150 MB file size cap",
      "500 questions / month",
      "25 Premium answers (GPT-5.5) / month",
      "1-year history retention",
    ],
  },
  {
    id: "business",
    name: "Business",
    price: 39,
    blurb: "For teams. Billed per seat; enterprise features on request.",
    cta: { label: "Contact us", href: "mailto:hello@helpmateai.xyz" },
    features: [
      "Everything in Pro",
      "500 MB file size cap",
      "2,000 questions / seat",
      "100 Premium answers / seat",
      "Unlimited history retention",
      "Team seats — on request",
      "SSO + audit log — on request",
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

// Resolves the right CTA href + label per tier. Free always goes to
// the workspace (sign-in flow). Business is a mailto. Pro routes to
// Lemon Squeezy hosted checkout when (a) LS env vars are configured
// AND (b) a Supabase user is signed in -- otherwise it falls back to
// the workspace URL so the user signs in first; once they return to
// the pricing page logged in, the Pro CTA flips to the LS URL.
function resolveCta(
  tier: Tier,
  userId: string | null,
): { href: string; label: string; disabled: boolean } {
  if (tier.id === "business" || tier.id === "free") {
    return { href: tier.cta.href, label: tier.cta.label, disabled: false };
  }
  // Pro tier: LS checkout if available, workspace sign-in URL otherwise.
  if (!isLemonSqueezyEnabled()) {
    return { href: tier.cta.href, label: "Coming soon", disabled: true };
  }
  if (!userId) {
    return {
      href: tier.cta.href,
      label: "Sign in to subscribe",
      disabled: false,
    };
  }
  const checkoutUrl = getCheckoutUrl("pro", userId);
  if (!checkoutUrl) {
    return { href: tier.cta.href, label: "Coming soon", disabled: true };
  }
  return { href: checkoutUrl, label: tier.cta.label, disabled: false };
}

function PricingCard({ tier }: { tier: Tier }) {
  const isFeatured = Boolean(tier.featured);
  // Read the signed-in user's id once on mount via the browser
  // Supabase client. We don't subscribe to auth state changes here --
  // the pricing page isn't long-lived and a user signing in mid-view
  // would still click through to the workspace URL fallback, which
  // bounces them through the OAuth flow + back here. Re-renders the
  // CTA on the next mount.
  const [userId, setUserId] = useState<string | null>(null);
  useEffect(() => {
    if (!isSupabaseConfigured()) return;
    let cancelled = false;
    const supabase = createClient();
    supabase.auth
      .getUser()
      .then((response: { data: { user: { id: string } | null } }) => {
        if (cancelled) return;
        setUserId(response.data.user?.id ?? null);
      })
      .catch(() => {
        // Auth read failed (transient network / dev without supabase).
        // Fall through to the signed-out CTA path.
      });
    return () => {
      cancelled = true;
    };
  }, []);
  const cta = resolveCta(tier, userId);

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
      <a
        className="l-pricing-cta"
        href={cta.disabled ? undefined : cta.href}
        aria-disabled={cta.disabled || undefined}
        onClick={
          cta.disabled
            ? undefined
            : () =>
                capturePostHogEvent("upgrade_clicked", {
                  tier: tier.name,
                  source: "pricing",
                })
        }
        // When LS isn't configured yet, the CTA renders as a static
        // "Coming soon" label without a destination -- a11y peers
        // call this the most honest fallback (no broken link, no
        // misleading enabled state).
      >
        {cta.label}
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
