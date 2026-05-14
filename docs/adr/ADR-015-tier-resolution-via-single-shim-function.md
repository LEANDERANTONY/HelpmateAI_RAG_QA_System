# ADR-015: Tier Resolution Via A Single Shim Function

Date: 2026-05-15

Status: Shipped

## Context

The tier-enforcement series introduced six gates across the backend: file size at upload, doc count at upload, monthly question quota at `/qa`, premium-answer quota at `/qa`, tier-aware answer model selection, and tier-aware workspace retention in the sweeper. Each gate needs to know which tier the request's user is on. Without a deliberate abstraction, every gate would have its own snippet for "look up the user's subscription" — which is fine on day one and a nightmare the moment payment integration ships, because every gate has to be re-edited in lockstep with the lookup change.

Three properties drove the design:

- Payment integration was planned but not yet started when the gates were written. The gates had to ship first (the COGS argument doesn't wait for a payment processor); subscription lookup had to land second without touching the gate call sites
- The lookup sits on a hot path. `/qa`, `/documents/upload`, and `/workspace/quota` all resolve the tier on every call. A network round-trip per gate (or worse, per gate * per request when several gates fire per request) would shred P95
- The product would likely add a second payment processor over time (Stripe + Razorpay for India once the sole prop is registered, on top of the v1 Lemon Squeezy MoR — see ADR-018). The resolver would eventually need to read from multiple subscription sources, not just one

## Decision

Introduce a single shim function in `backend/tiers.py`:

```python
def resolve_user_tier(user: AuthenticatedUser) -> Tier:
    ...
```

Every gate routes through this function and reads its limits from `TIER_LIMITS[tier]`. The function takes only the authenticated user and is intentionally narrow: no `org_id`, no `request` context, no kwargs. When Business-tier seat lookups eventually ship, the signature changes here, once, instead of at every call site.

The matrix is a `dict[Tier, TierLimits]` typed via a `TypedDict`, with `Tier = Literal["free", "pro", "business"]` so any gate that misspells a tier fails static type-check. The `TIER_LIMITS` source-of-truth lives next to the resolver — the pricing UI in `frontend/src/components/landing/pricing.tsx` is the only other place these numbers appear, and drift between the two is documented as a one-PR-touches-both-files rule.

The initial implementation returns `"free"` for every user. The signature is wired through every gate from the first PR (commit `97be986`) onward, so the Lemon Squeezy scaffold (ADR-018) only needed to change the body of `resolve_user_tier` — not the upload handler, the `/qa` handler, the cache key builder, or the retention sweeper.

### Alternatives considered

- **Per-gate hardcoded checks** — every gate calls into Supabase directly. Rejected because the inevitable schema change at payment integration time would require N coordinated diffs, and because the LRU-cache strategy (see below) is hard to share across gates without a central function.
- **Decorator-based gates** (`@require_tier("pro")`) — looked tempting for `/qa` but quickly fell apart. The premium-answer gate is dynamic (depends on a request body field), the doc-count gate needs the user's store, and the retention sweeper isn't even on a request path. A decorator only handles the simplest cases and forces an awkward second pattern for everything else.
- **A class-based `TierContext` injected via FastAPI dependency** — more "framework-correct" but mostly ceremony. The shim is one function, takes one argument, returns one value. Wrapping it in a class adds an injection boundary without adding any actual decoupling.

### The cache

`resolve_user_tier` reads through `backend.subscriptions.get_active_subscription`, which is wrapped in an LRU cache keyed by `(user_id, current_minute_bucket)`. The cache key changes every calendar minute, so reads converge on a new subscription state within at most 60 seconds without the webhook having to invalidate anything. The Lemon Squeezy webhook handler does invalidate the cache on every upsert (for a sharper user-visible cutover after checkout), but the natural minute-bucket expiry is the contract — a webhook delivery failure can't strand a user on the wrong tier for more than a minute.

The cache holds at most 4096 entries (~10MB) and evicts LRU on overflow. At our scale the cache hit rate during a single user session is effectively 100% — the gate fires several times per request and the entire chain reads through one cached result.

## Consequences

The architectural payoff landed on Day 33: the Lemon Squeezy scaffold was a four-PR feature branch, not a six-PR per-gate retrofit. The webhook + table + reader landed independently of the gates, and `resolve_user_tier` got a 30-line implementation change that consulted the `subscriptions` table while the rest of the stack was untouched.

The signature stays narrow on purpose. When seat-based Business-tier billing ships, the resolver will likely need to take an `org_id` or pre-resolve the org from the user. That's a known future change documented inline in `backend/tiers.py`. Until then, accepting just `AuthenticatedUser` keeps every call site simple and prevents speculative generality.

The fallback semantics are conservative. The resolver returns `"free"` for: no user, no subscription row, unknown tier value (defensive against a future migration that adds a tier the backend doesn't know about), status not in `{"active", "cancelled", "past_due"}`, or `current_period_end <= now`. Anything else also returns `"free"`. The bias is deliberate — a misclassification on the Free side surfaces as a user contacting support; a misclassification on the paid side surfaces as a stranger getting premium models.

`settings.answer_model` is preserved as the unauthenticated fallback. Eval scripts and any context that runs without a logged-in user (the smoke audit, the offline benchmark harness) continue to read the env var directly. Only the `/qa` handler overrides it with `model_override=TIER_LIMITS[tier]["answer_model"]`. The cache key for answers includes the active model so a Free `nano` answer can't be served back to a Pro user asking the same question — free and paid tiers have separate cache namespaces, which is the correct behavior but worth flagging when telemetry ladders against cache hit rate by tier.

## Validation

Unit tests in `tests/backend/test_tiers.py` verify:

- `resolve_user_tier(None)` returns `"free"`
- A user with no subscription row returns `"free"`
- An `active` row with `current_period_end > now` returns the subscription's tier
- A `cancelled` row with `current_period_end > now` retains the tier (paid period not yet elapsed)
- A `past_due` row retains the tier (LS dunning window)
- `expired` / `paused` always return `"free"`
- Unknown tier values fall back to `"free"`
- `current_period_end <= now` falls back to `"free"`
- The LRU cache returns the same instance on repeat calls within the minute bucket
- A monotonic-cap invariant test (`test_higher_tier_never_has_smaller_cap`) ensures Pro never beats Business on any axis. Intentional — if a future tier adds an inverted limit (e.g. "Business has stricter audit retention"), the test needs a per-field override list rather than a silent pass.
