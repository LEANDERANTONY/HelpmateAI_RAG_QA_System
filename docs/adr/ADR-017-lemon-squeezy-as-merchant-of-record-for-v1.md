# ADR-017: Lemon Squeezy As Merchant Of Record For v1 Billing

Date: 2026-05-15

Status: Shipped (scaffold), live mode pending LS KYC

## Context

The tier-enforcement series (Day 32) shipped the gates with `resolve_user_tier` returning `"free"` for every user. The next step was binding tier resolution to a real subscription system so paid tiers can actually be sold.

Three constraints shaped the choice of processor:

- **Solo developer, unregistered in India.** Stripe doesn't onboard Indian sellers without a registered legal entity. Razorpay does, but only to Indian customers, which excludes the entire international audience the product is built for. Both options would have required the seller to register a sole proprietorship and a GST number — workable, but a multi-week side-quest that has to clear before any revenue can land.
- **Global reach from day one.** The landing page (Day 28) is hosted on `helpmateai.xyz` and the workspace surface is reachable from anywhere. Restricting billing to one geography was off the table — the eval narrative, the open-source positioning, and the validation snapshot all assume an international audience.
- **Sales tax, VAT, and chargeback risk had to be off the founder's plate.** A solo developer cannot meaningfully comply with EU VAT, UK VAT, US sales tax, and India GST simultaneously, especially while still building product. The Merchant of Record (MoR) model exists precisely so independent developers can sell internationally without becoming a tax-compliance team of one.

## Decision

Use Lemon Squeezy as the v1 payment processor, in the Merchant of Record model. LS becomes the legal seller to the end customer; we become a vendor to LS. LS handles tax collection, remittance, invoicing, EU VAT-MOSS, US sales tax nexus, and chargebacks. The product's `subscriptions` table records LS's view of subscription state via webhooks.

### Architectural neutrality

The `subscriptions` table includes a `processor` column (TEXT, currently always `"lemonsqueezy"`) and a `processor_subscription_id` column. The shape is deliberately processor-agnostic — when a future migration adds Stripe rows (after sole-prop registration clears), they sit in the same table with `processor="stripe"` and a different ID format. `resolve_user_tier` doesn't care which processor created the row; it only reads the normalized `tier`, `status`, and `current_period_end` fields.

```
subscriptions
├── user_id                          (UUID, PK)
├── processor                        ("lemonsqueezy" | "stripe" | ...)
├── processor_customer_id            (LS customer id, Stripe customer id, ...)
├── processor_subscription_id        (LS subscription id, Stripe sub id, ...)
├── tier                             ("pro" | "business")
├── status                           ("active" | "cancelled" | "past_due" | ...)
├── current_period_end               (timestamptz, nullable)
├── cancel_at_period_end             (bool)
└── variant_id                       (LS variant id, Stripe price id, ...)
```

A second processor only needs:

1. A webhook handler that maps that processor's events to our status vocabulary
2. A way to derive tier from the processor's variant/price ID
3. A frontend CTA branch that opens the right hosted checkout

`resolve_user_tier`, the gates, the cache, the table schema — all stay the same.

### Event mapping

LS webhook events map to our internal status vocabulary in `backend/webhooks/lemonsqueezy.py`:

| LS event | `status` written | `cancel_at_period_end` | Tier impact |
| --- | --- | --- | --- |
| `subscription_created` | `active` | false | Grants tier from variant_id. |
| `subscription_updated` | `active` | from payload | Refreshes row from payload. |
| `subscription_cancelled` | `cancelled` | true | Tier kept until `current_period_end`. |
| `subscription_resumed` | `active` | false | Cancellation reverted. |
| `subscription_expired` | `expired` | — | Terminal downgrade to Free. |
| `subscription_paused` | `paused` | — | Soft downgrade to Free. |
| `subscription_unpaused` | `active` | — | Tier restored. |
| `subscription_payment_success` | `active` | — | `current_period_end` refreshed. |
| `subscription_payment_failed` | `past_due` | — | Tier kept during dunning. |
| `subscription_payment_recovered` | `active` | — | Dunning cleared. |

The status vocabulary is the same one a Stripe handler would write — `"past_due"` is Stripe's term, `"cancel_at_period_end"` is Stripe's field name. The choice to mirror Stripe's vocabulary is deliberate: when the second processor lands, the mapping table doubles in size but the readers don't change.

### Idempotency

LS retries on non-2xx and has at-least-once semantics. Two layers protect against duplicate processing:

1. `subscription_webhook_log` (PK = `event_id` derived from `meta.webhook_id`). A duplicate delivery short-circuits to `{"status": "duplicate"}` and returns 200, so LS stops retrying.
2. The `subscriptions` upsert is keyed on `user_id` and idempotent by construction — even if the log lookup fails open, re-running the upsert produces the same row.

### Signature verification

The webhook handler verifies `X-Signature` (hex-encoded HMAC-SHA256 of the raw body, signed with `HELPMATE_LEMONSQUEEZY_WEBHOOK_SECRET`) via `hmac.compare_digest`. Failure → 401, no row mutated, no log entry written. Constant-time compare is non-negotiable; a timing-attack-vulnerable compare would let an attacker leak the secret one byte at a time.

### Env-gated fallback

LS is still in KYC review as of the scaffold landing. The branch had to be shippable into `main` regardless:

- Backend: when `HELPMATE_LEMONSQUEEZY_WEBHOOK_SECRET` is unset, `POST /webhooks/lemonsqueezy` returns 503 with `Retry-After: 300` (5 minutes). LS retries on 5xx but with backoff, and once the secret is set in production the next retry succeeds.
- Frontend: when `NEXT_PUBLIC_LEMONSQUEEZY_STORE_ID` is unset, the Pro pricing CTA renders "Coming soon" (disabled) and Business falls back to the existing `mailto:` for enterprise contact. No JavaScript console errors, no broken redirects.

The env-gating means the four-PR scaffold can merge into `main` and ship to production before LS approves the live store. When the live credentials land, two env-var changes flip the surface from "Coming soon" to live checkout without a code deploy.

## Consequences

The product can be sold internationally from day one without the founder registering as a tax-compliance entity in every jurisdiction the product reaches. LS handles EU VAT, UK VAT, US sales tax nexus, and India GST on inbound revenue. The trade-off is LS's 5% + processor fees on every transaction — meaningfully higher than Stripe's 2.9% + 30¢, but the comparison isn't apples-to-apples because Stripe doesn't cover tax/chargebacks/compliance at that rate.

The architectural cost of "neutral processor column" is paid once and amortized indefinitely. The `processor` column adds zero complexity to readers (`resolve_user_tier` reads three fields and doesn't care about the source), and the value of being able to drop Stripe in later without a schema migration is large.

The migration path to Stripe + Razorpay is well-defined. When the sole prop is registered:

1. Add Stripe webhook handler (mirror of LS's, different vendor SDK)
2. Add Razorpay webhook handler for Indian customers
3. Add a processor-routing decision at checkout time (likely "use Razorpay if billing country = India and amount in INR, otherwise Stripe")
4. Keep LS as a fallback during a transition window so existing subscriptions don't have to migrate

None of this touches the gates, the resolver, or the schema. A subscription that started under LS keeps renewing under LS until the customer churns or upgrades; new subscriptions can route to the new processor.

The "Coming soon" CTA is honest. A user who lands on the pricing page before LS KYC clears sees that paid tiers exist, sees the prices, and sees that they're not yet sellable. The mailto fallback for Business is a path for enterprise interest to actually surface. The toast/state machine around the "Coming soon" → "Live" transition is intentionally minimal — when LS goes live, the next page load shows the live checkout button, no announcement banner, no migration prompt.

### What we deliberately did not build

- A custom checkout page. LS hosted checkout is the right surface — it handles 3DS, card-on-file, SCA, country-specific payment methods (iDEAL, Bancontact, etc.) — none of which are useful for us to reimplement
- A self-service tier downgrade flow. LS hosted customer portal handles cancellation, payment method changes, and invoice history. We link out to it; we don't duplicate it
- A retry/dunning UI on the workspace. The `past_due` status preserves the user's tier during LS's dunning window; the user sees a soft banner if/when dunning fails and the status transitions to `expired`. We don't run a parallel dunning state machine
- Multiple-currency display on the landing. LS handles currency conversion at checkout; the pricing UI quotes USD because that's the LS default and matches the international audience

## Validation

Unit tests in `tests/backend/test_lemonsqueezy_webhook.py` verify:

- Each LS event type produces the expected `status` write
- Signature mismatch returns 401 and does not mutate the table
- Unknown event names log + return 200 without writing
- Missing `user_id` in the custom data logs + returns 200 without writing
- Duplicate `webhook_id` short-circuits to `{"status": "duplicate"}` and returns 200
- A 503-fallback fires when `HELPMATE_LEMONSQUEEZY_WEBHOOK_SECRET` is unset

Frontend smoke verification:

- With `NEXT_PUBLIC_LEMONSQUEEZY_*` unset, the Pro CTA renders "Coming soon" and is disabled; Business CTA renders the mailto link
- With the env vars set, the Pro CTA routes to `https://<store>.lemonsqueezy.com/checkout/buy/<variant>?checkout[custom][user_id]=<supabase uid>`
- The `?checkout[custom][user_id]=...` parameter arrives in the LS webhook payload under `meta.custom_data.user_id`, which `process_webhook` reads to attribute the new row

Sandbox end-to-end:

- A test-card checkout in LS sandbox produces a `subscription_created` webhook delivery within ~5s
- The webhook handler writes the row to `subscriptions` and the log to `subscription_webhook_log`
- The next `/workspace/quota` call sees the new tier (within the 60s LRU minute bucket — or immediately if the webhook handler invalidated the cache, which it does)
- Cancellation in the LS portal produces a `subscription_cancelled` webhook; the row updates to `status="cancelled"` and `cancel_at_period_end=true`; `resolve_user_tier` continues to return the paid tier until `current_period_end` elapses

The full sandbox → live cutover plan is documented in `docs/lemon-squeezy.md`. When KYC clears, the plan is: regenerate API key + webhook secret in live mode, swap env vars on the VPS, re-register the webhook URL in the live dashboard, and verify a single live test transaction before announcing.
