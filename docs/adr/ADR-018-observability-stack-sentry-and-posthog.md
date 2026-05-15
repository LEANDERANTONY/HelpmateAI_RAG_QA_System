# ADR-018: Observability Stack — Sentry + PostHog with Consent-Gated Analytics

Date: 2026-05-15

Status: Shipped

## Context

By the end of Day 33 (tier enforcement + LS scaffold), HelpmateAI was running in production with no first-class crash reporter on the backend, no LLM-cost attribution beyond the per-trace `helpmate_run_traces` table, and no user-cohort analytics. Vercel auto-tags the frontend (Speed Insights + Analytics) but the FastAPI container had nothing — a 500 from `/qa` would land in `docker logs helpmate-api` and stay there until someone happened to look.

Three things made adding an observability layer urgent now rather than after first revenue:

- **Payment cutover is close.** When the LS variant IDs flip live, the dashboards need to already show free-vs-pro cohort behavior + LLM cost per tier. Retrofitting after a paid user signs up is the wrong direction of dependency.
- **AI Agents Monitoring is now a first-class Sentry feature.** The `OpenAIIntegration` auto-emits AI-aware spans (token counts, model, latency, cost) without per-call instrumentation. Free on the Developer plan. The retrieval pipeline makes 5-7 LLM calls per `/qa` and attributing slowness to "router vs generator vs verifier" used to need a manual debug print pass.
- **PostHog free tier is generous enough for an MVP.** 1M events/month + 5K replays/month + 100K exceptions covers far more traffic than the product will see this year.

The third constraint was **GDPR / ePrivacy posture**. HelpmateAI's backend is in Frankfurt (EU), the PostHog and Sentry projects are EU-region, and EU users hitting the landing page can't be tracked without explicit consent. The integration had to make that compliance posture explicit at the SDK level, not as a layer of legal disclosure on top of a leaky baseline.

## Decision

Adopt a two-vendor observability stack:

- **Sentry** for error tracking, performance traces, AI Agents Monitoring, Logs, Crons, and session replay (errors-only). One Sentry org (`leander-antony-a`) hosts two projects per product: `helpmate-backend` + `helpmate-frontend`. Backend Python integrations: `FastApiIntegration`, `StarletteIntegration`, `LoggingIntegration`, `OpenAIIntegration` (`include_prompts=False` for PII). Frontend integrations: `feedbackIntegration` (always on, legitimate interest), `replayIntegration` (consent-gated, masked).
- **PostHog** for product analytics, session replay, identify/group cohort analytics, and LLM Analytics (`$ai_generation` events). Free Developer plan caps at 1 project per org, so HelpmateAI and AI Job Agent share the same project (179885, EU region); every event carries a `product: "helpmate"` or `"jobagent"` super-property so dashboards can slice cleanly.

Both clients are bootstrapped via a single module (`backend/observability.py`) at import time, before `FastAPI()` is constructed so the Sentry ASGI middleware wraps the app at startup. The module is a no-op when either DSN / API key is empty, so dev and CI run unchanged.

### Free-tier-maxed configuration

| Sentry feature | Setting | Why |
| --- | --- | --- |
| Tracing | `traces_sample_rate=0.1` | 10% sample is the Sentry default; bumpable via env if a feature needs deeper coverage |
| Profiling | `profiles_sample_rate=0.05` | 5% keeps free quota healthy while surfacing slow code paths |
| Logs | `enable_logs=True` | New Sentry Logs product, separate from breadcrumbs, full-text searchable |
| Replay (FE) | `replaysSessionSampleRate=0, replaysOnErrorSampleRate=1.0` | No ambient session sampling (PostHog handles full session replay), but 100% on errored sessions for high-signal debugging |
| User Feedback widget | always on | Tied to current Sentry session — user reports include breadcrumbs + active replay |
| Crons | per-job `monitor_slug` | Used by `nightly_eval` to fire missed-heartbeat alerts; gated by env var (see ADR-020) |
| Uptime monitor | 1 per project | 5-min `/health` poll, 3-failure threshold, alerts via project email default |
| AI Agents | `OpenAIIntegration` | Per-LLM-call spans with token + cost + model + latency |

| PostHog feature | Setting | Why |
| --- | --- | --- |
| Autocapture | on | Click + form submit capture without per-event wiring |
| Session replay | `maskAllInputs: true` | Free tier covers 5K replays/mo; PII-safe by default |
| Heatmaps | on | Workspace UX iteration signal |
| Surveys | on (project-level toggle) | Future NPS / feedback survey wiring |
| Exception capture | **off** | Sentry is the source of truth; avoid double-billing the free-tier exception budget |
| Authorized URLs | prod + Vercel previews + localhost | Required for Web Analytics + toolbar |
| Recording domains | same allowlist | Gates session replay |

### Backend-side $ai_generation events

Every LLM call inside a `/qa` request emits a PostHog `$ai_generation` event with the documented property schema:

```
{
  "$ai_trace_id": "trace-<hex>",
  "$ai_span_name": "answer_generator" | "query_router" | "support_verifier" | ...,
  "$ai_provider": "openai",
  "$ai_model": "<model-name>",
  "$ai_input_tokens": int,
  "$ai_output_tokens": int,
  "$ai_total_cost_usd": float,
  "$ai_is_error": bool,
  "tier": "<free|pro|business>",
  "document_id": "<id>",
  "product": "helpmate"
}
```

The pipeline pulls per-call breakdown off `cost_collector.to_payload()` **after** the answer cache write so cache hits don't replay events that never happened on this request. PostHog's LLM Analytics dashboard groups events by `$ai_trace_id` for the per-request waterfall view.

### Consent gating (Day 35 banner — see ADR-019)

Sentry split into two integration categories:

- **Always-on** (legitimate interest under GDPR Art. 6(1)(f)): error tracking, traces, Feedback widget. These load regardless of cookie banner state — crash reporting is operationally necessary.
- **Consent-gated** (requires explicit opt-in): Session Replay. Loads only when `localStorage["helpmate-cookie-consent"] === "accepted"`. The state-change event listener hot-adds the Replay integration via `Sentry.addIntegration(...)` without a page reload when consent flips.

PostHog is fully consent-gated: `posthog.init` only runs after consent acceptance. State changes call `posthog.opt_in_capturing()` / `opt_out_capturing()` for runtime flips.

### Vercel-Sentry integration vs manual env vars

The Sentry-Vercel marketplace integration auto-provisions `SENTRY_AUTH_TOKEN` + `NEXT_PUBLIC_SENTRY_DSN` and creates release markers per Vercel deploy. It was installed for HelpmateAI's `helpmateai` Vercel project. For AI Job Agent, the integration's env-var-upsert step conflicted with already-set vars (the manual setup happened first), so the manual fallback was used. Both paths give the same source-map upload behavior; only the auto-created release markers are missing in the manual case (those can be backfilled from `VERCEL_GIT_COMMIT_SHA` via `withSentryConfig` if needed).

## Consequences

### Positive

- **Single bootstrap path.** `initialize_observability(settings)` is the only place the two clients are touched. Adding a third vendor (e.g. Datadog APM, if scale ever justifies) means adding one call in that function and leaving every route handler unchanged.
- **No-op safe defaults.** Empty DSN / API key → SDK init is skipped → zero network calls, zero memory cost. Local dev, CI, and the test suite run without observability wiring.
- **Pytest-skip guard.** The `_running_under_pytest()` check skips Sentry entirely when `PYTEST_CURRENT_TEST` is set. The first deploy fired ~50 test events into prod within an hour before this was added (HELPMATE-BACKEND-2 through 7); the guard closed that hole permanently.
- **HTTPException filter.** `before_send` drops intentional 4xx flow control + 5xx "not configured" / "temporarily unavailable" guards. Keeps the issue feed focused on genuine bugs (RuntimeError, IntegrityError, OpenAI APIError, etc.) instead of "user uploaded a 26MB file at a 25MB cap" noise.
- **Shared PostHog project with `product` tag.** Both repos feed one project but dashboards stay cleanly separable. Free tier accommodates the combined traffic with room to spare.

### Negative

- **Two vendors instead of one.** Sentry + PostHog have overlapping replay capability. We picked Sentry-Replay for errored sessions (high-signal) and PostHog Replay for ambient session sampling (volume) — that split is defensible but adds a second SDK to load on the browser bundle. Bundle size cost: ~80KB gzipped combined.
- **Free tier quota is finite.** 5K errors / 50 FE replays / 1M PostHog events per month. If a user-facing bug fires a tight loop of errors, the quota burns fast. The HTTPException filter mitigates this for backend; the `replaysOnErrorSampleRate=1.0` is the highest-risk knob (one error per replay).
- **GDPR posture depends on a custom banner.** ADR-019 captures the consent banner; if that banner breaks (e.g. state never persists), analytics silently never load. Mitigation: a small click-test in the deploy smoke check would catch regressions, but it's not yet automated.

### Neutral

- **PostHog free-tier 1-project limit forced the product-tag pattern.** If we ever upgrade to a paid plan with multi-project support, the migration is mechanical (move events with `product=jobagent` to a new project, update the API key in Job Agent's `.env`).
- **Sentry release tagging via GitHub commit SHA**, not Vercel deploy ID — works the same for HelpmateAI (integration-managed) and Job Agent (manual env var). Suspect Commits feature works in both cases because the GitHub integration is the source of commit context.

## Alternatives considered

- **LogRocket + custom error reporter.** LogRocket has stronger session replay but no free tier above 1K sessions/month. Rejected on cost.
- **Datadog APM.** Far more capable but priced for series-A startups, not solo MVPs. Rejected on cost.
- **PostHog alone (using PostHog's exception tracking).** PostHog covers errors as a feature, so a single-vendor stack is tempting. Rejected because Sentry's Python + JS error groupings are categorically better (stack-trace fingerprinting, release tracking, code mappings to GitHub) and free-tier Sentry doesn't compete with PostHog's free-tier event budget.
- **Self-hosted observability (Plausible + Glitchtip).** Theoretical $0 cost but operational burden of another container set on the VPS. Rejected as scope creep.
- **Skip observability until first revenue.** Tempting but wrong direction of dependency — without observability we can't measure free-vs-pro cohort behavior, which is the data the payment cutover analysis needs.

## References

- DEVLOG Day 35: "Sentry + PostHog Observability Stack End-To-End"
- `backend/observability.py` — single bootstrap module
- `frontend/instrumentation-client.ts`, `instrumentation.ts`, `sentry.server.config.ts`, `sentry.edge.config.ts`
- `frontend/src/components/posthog-provider.tsx`, `cookie-consent.tsx`
- ADR-019: EU cookie consent banner + GDPR-aligned analytics gating
- ADR-020: Manual-only nightly_eval at pre-revenue stage
- ADR-013: Ephemeral workflow run traces (the per-request cost-tracking layer that feeds `$ai_generation`)
