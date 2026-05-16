# DEVLOG - HelpmateAI

This document tracks the major implementation changes, the problems we hit, and how we improved the system.

Historical note:

- earlier entries reflect the first app baseline
- later entries add quality-control, benchmarking, and document-intelligence work on top of that baseline
- the project is still evolving, so later entries refine earlier architectural assumptions without erasing them

## Day 37: Ingestion Overhaul, Read Mode D1/D2/D3 Closure, Abstention Regression Hunt, Pricing Truthfulness

A 29-commit batch (local, unpushed — see Status). Headline: the DOCX ingestion + Read Mode architecture was reworked to fix the cited-passage navigation defects at the root, a multi-day regression audit found and fixed the Safety Pack over-tightening the abstention layer, and a landing-vs-code audit removed fabricated pricing claims.

### Ingestion + Read Mode (the D1/D2/D3 defect family)

- **DOCX now ingests through its LibreOffice PDF rendition** (`0b2e402`). The python-docx path flattened every DOCX into one `page_label="Document"` page and dropped tables/headers/footers entirely — the root cause of the Read Mode "DOCX page hint is dead → ring trap" defect (D3): `parsePageLabel("Document")→1`, the viewer's ±3 ring only ever scanned rendered-PDF pages 1-4, so a citation on page ≥5 fell to a no-match banner with the real match painted off-screen. Fix: `_prepare_docx_rendition` stages the LibreOffice PDF at the exact `{document_id}.pdf` path `normalize_upload_paths` already computes (single conversion, cache-reused), and `ingest_document(..., docx_pdf_rendition=...)` routes DOCX through the proven native-PDF path (`_extract_pdf` → pypdf text + pdfplumber tables + real `Page N`). DOCX chunk page labels now line up **by construction** with the same PDF the viewer serves. Falls back to python-docx when LibreOffice is unavailable (self-consistent: the viewer is download-only there too).
- **Table extraction generalized** (`6171d72`). The `_looks_table_enrichment_candidate` pre-gate was a corpus-word allowlist (`scenario`, `2050`, `gtco`, `usd`…) fitted to the FinanceBench/climate eval corpus — every generic table (HR, pricing, schedules) was silently skipped. Replaced with a vocabulary-free structural signal (captioned `Table/Exhibit N` or ≥N rows with column structure / numeric density; `len(tokens)>=5` proxy kept because pypdf collapses cell boundaries to single spaces). Detection now `lines`-first with a per-page `text` fallback only where `lines` found nothing (borderless coverage without paying the text-strategy hallucination tax everywhere); shape filter kept as a shared precision guard. Page cap default `40 → 0` (unlimited): a table on page 150 of a 200-page report must still be captured. New env knobs `HELPMATE_TABLE_PREGATE_MIN_LINES` (default 3), `HELPMATE_TABLE_EXTRACTOR_MAX_PAGES` (default 0 = unlimited, safety-valve only).
- **Read Mode D1/D2 closed** (`5e40204`, `fec9de0`, `ec62751`). D1: the find dispatch's document-wide `highlightAll` painted the anchor on every page; now the highlight is scoped to the resolved page (without breaking the scroll-to-`.highlight` mechanism). D2.1: `looksLikeBoilerplate` was over-stripping legitimate short first sentences as "headings", so the anchor could start mid-chunk — tightened. D2.2: multi-anchor — several short sub-phrases sampled across the chunk so the highlight spans the cited passage instead of an ≤80-char prefix. Prompt-drift verification confirmed all 7 registry prompts are byte-identical to their pre-registry inline form (PR#5 migration was faithful; not a regression source).

### Abstention regression hunt + fixes

- A reported "default recommended question → ABSTAINED" on a freshly ingested FOMC doc kicked off a full 5-6-day architectural-change audit. Two screenshots disproved the first two hypotheses (not schema-drift; not the verifier-working-correctly case) and isolated the real cause: **two coupled defects in the Safety Pack (PR#6 `a9245d5`), amplified by free-tier model routing**.
- **`verify_support_status` false abstention** + **the line-356 re-stamp** (`09d4f07`). Once an answer acknowledged any gap, the supported→partial→unsupported cascade had no path back to "supported" even when the verifier itself found grounded `supported_facts` and an empty `missing_or_ambiguous_facts`; and even after the verifier returned "supported", `supported=True` was gated on `not _answer_reports_support_gap(answer_text)`, so a polite caveat kept `supported=False` and `elif not supported` re-stamped "unsupported". A fully-grounded, correctly-cited answer was abstained purely for hedging. Fixed: the verifier's evidence-grounded verdict wins over the answer's self-doubt; genuine verifier-found gaps still → partial/unsupported (over-correction guard test added).
- **Amplifier removed**: free-tier `answer_model` `gpt-5.4-nano → gpt-5.4-mini` (`09d4f07`). Nano's looser JSON shaping + heavier hedging is what made the two defects fire on the default path. Quality-over-COGS; free-tier answer cost rises (documented in `backend/tiers.py`).
- **Schema-strict posture** `extra="forbid" → "ignore"` on `StructuredLLMModel` (`fad5c47`). Defence-in-depth, scoped honestly: the OpenAI structured-outputs strict `response_format` (`_enforce_strict_schema` force-sets `additionalProperties:false` independently) is the real fail-closed guard, so this only relaxes the redundant client-side re-validation — a benign extra key when strict mode isn't honoured no longer nukes the whole answer; required-field/type/enum enforcement is unchanged.
- **Hardening**: a golden-hash byte-identity guard over all 7 active registry prompts (`test_prompt_registry.py`) — the PR#5 migration shipped without one; a future `v1.json` edit dropping a required key would otherwise silently universal-abstain via the schema gate.
- Verified the rest of the 5-6-day surface: retrieval/ingestion LLM call sites (router/planner/landmarks/classifier) all degrade gracefully on drift (heuristic / deterministic plan / no-enrichment / keyword fallback) — the dangerous strict-gate class is confined to the /qa answer+verifier path, now fixed. File-storage fails loud (FileNotFound, not silent-empty); retention sweeper is inactivity-TTL with activity refresh (not creation+30d); quota gate order is correct. None architecture-breaking.

### Pricing truthfulness (landing vs. wired code)

- `70b19c9`: Pro **"Export to Word + Notion"** had zero implementation (no export endpoint, "notion" appears nowhere in the repo; only an ungated plain-text "Copy answer"). On a self-serve checkout tier that's a false-advertising liability — removed (propagates to Business via "Everything in Pro"). Business **"5-seat team workspace" / "SSO + audit log"** — no team/seat/SSO/audit anywhere; reframed as "on request" (Business is sales-led/Contact-us), blurb softened.
- `b514457`: the product is **single-document by design** — `_find_active_workspace_document` keeps the one most-recent doc and deletes all others on every workspace resolve ("one workspace per user"; no document list endpoint; no switcher). So Free "3 active documents" and Pro "Unlimited documents" (and the blurbs carrying the same claim) misrepresented the core model — removed; blurbs reblurbed to real wired differentiators; the COGS comment reconciled with a guard note that doc-count is deliberately not a tier lever. Every remaining bullet on every tier is now verified against `backend/tiers.py` + the live gates.

### Quota/tier UX + UI polish

- Quota-UX P1/P2/P3 (`637c7e0`, `425a87d`, `375f30c`, `c7fcf5e`): correct quota-limit framing + an upgrade CTA on the locked Premium toggle, themed CTA using brand hex (not `var(--accent)`), and surfacing workspace retention so the sweep isn't silent.
- Mobile/Read-Mode/command-palette polish + cookie-consent (the 2026-05-16 sub-batch `889ec3c`…`771ba16`): mobile PDF edge-to-edge / fit-overshoot / touch-scroll inside the vaul sheet, single-line ask-row, command-palette mint styling + Sections-group removal, person glyph for signed-out, answer-feedback form CSS, always-visible Premium toggle, dropped the Read Mode abstention banner, and cookie consent persisted in a parent-domain cookie instead of localStorage.

Status: the full 29-commit batch is **local and unpushed** (HelpmateAI is ~29 ahead of `origin/main`). It is held deliberately behind one gate.

Why now:

- the cited-passage navigation defect (D3 ring trap) was a root-cause ingestion problem, not a viewer patch — fixing it where the page label is born (the rendition) also recovered DOCX table/header/footer content that python-docx silently dropped
- the abstention regression was systemic (a confirmed logic bug + a model-downgrade amplifier landing in the same window with no integration eval between them) — exactly the class of drift the deferred ADR-020 eval exists to catch
- promising unbuilt features on a self-serve paid tier is a consumer-protection liability the moment the Lemon Squeezy variant goes live; the single-document positioning is the headline product-model claim and was misrepresented on every tier including Free

Challenges:

- the abstention bug was two *coupled* defects — fixing only the verifier cascade left it still broken; the failing regression test (not the code read) is what surfaced the second defect downstream
- the prompt-drift hypothesis had to be settled by direct byte comparison of all 7 prompts against their pre-registry inline form, not assumed; it was clean, which sharpened the diagnosis to the Python logic
- "scanned, not verified" is not "safe" — the medium-risk register items (file-storage, retention sweeper, quota gate, support_summary) each needed their actual failure path read before they could be cleared

Improvements:

- DOCX Read Mode page hints are now correct by construction (extracted from the exact PDF the viewer serves), and DOCX table/header/footer text is captured for the first time
- the abstention layer no longer punishes a well-grounded answer for honestly hedging; the prompt-drift golden-hash guard makes a future silent prompt regression a hard test failure
- the landing now promises only what the code delivers; the COGS comment carries an in-code guard against re-adding multi-doc copy

ADRs added:

- [ADR-021: DOCX ingestion via LibreOffice rendition + generalized table extraction](adr/ADR-021-docx-via-pdf-rendition-and-generalized-table-extraction.md)
- [ADR-022: Single-document workspace is the product model](adr/ADR-022-single-document-workspace-product-model.md)
- [ADR-023: Abstention-robustness posture after the Safety-Pack regression](adr/ADR-023-abstention-robustness-posture.md)

Eval gate (carried, not yet cleared):

- This batch changes ingestion AND the full abstention surface (verifier logic + default answer model + structured-output posture). Per ADR-020, run the manual eval on a pre-batch commit as baseline vs. current HEAD and compare FinanceBench / final-eval supported-rate **before pushing**. The expectation is neutral-to-up (the changes are recovery + correctness), but it is unverified until measured; the batch stays local until then.

## Day 36: Doc Hygiene + Operational Recovery + Cost-Aware Eval Pacing

- Switched `backend/nightly_eval` to **manual-only mode** (commit `2252dbd`). Real RAGAS + FinanceBench + final_eval runs cost ~\$1.5-3 in OpenAI per invocation (~600-1000 LLM calls); daily would burn \$45-90/month at pre-revenue stage. The Sentry Crons heartbeat is now gated on `HELPMATE_NIGHTLY_EVAL_MONITOR_ENABLED` (default off), and the cron line on the VPS was disabled with a documentation block describing the re-enable path. ADR-020 captures the cost rationale and the re-enable trigger (revenue justifying daily regression detection).
- Restored the Job Agent reverse-proxy block to the shared Caddyfile (commit `28509e1`). During the docker recreate that brought the new Job Agent observability image live, a `docker compose` cross-project name collision briefly bounced `helpmate-api`; restarting Caddy after the recovery cleared its in-memory runtime config, which had been silently carrying a `api.job-application-copilot.xyz` site block that was never committed. The committed Caddyfile only knew about `api.helpmateai.xyz`, so Job Agent's public domain went 502 until the missing block was added back to the file. Lesson: any runtime Caddy config edit needs to land in `deploy/vps/Caddyfile` immediately or the next restart wipes it. Cross-repo coupling note: the Job Agent backend's compose override (`AI_Job_Application_Agent/backend/vps/docker-compose.override.yml`) deliberately drops its own Caddy service via `shared_ingress / vps_default`, so the HelpmateAI Caddy is the single ingress for both products.
- Recovery from project-name churn. Bouncing `helpmate-api` with the wrong `-p helpmate` compose project name created fresh empty volumes (`helpmate_helpmate_*`) instead of remounting the original `vps_helpmate_*` volumes that hold real user data. Caught immediately via a volume-content check; restored by recreating with `-p vps` (matching the GitHub Actions deploy's default project name = directory name). Data was never lost — it was sitting on the original `vps_helpmate_indexes` (91 files) and `vps_helpmate_cache` (8 files) volumes the whole time, just unmounted from the running container. Volume audit added to the operational playbook in `docs/deployment.md`.
- Docker disk cleanup. 44 dangling images pruned via `docker image prune -f`, reclaiming 6.25 GB. Tagged images (`helpmateai/api:latest`, `ai_job_application_agent/api:latest`, `caddy:2`) preserved; all 5 named volumes preserved; all 3 active crontab entries preserved (the weekly Sunday `cleanup-docker.sh` cron is the long-term durable version of this).
- Repo hygiene pass. 9 merged remote branches pruned across both products (`feat/safety-pack-call-sites`, `feat/ux-pack`, `feat/observability-sentry-posthog`, `feat/prompt-registry-batch-2`, two `coderabbit-root` branches, two `docs/overnight-status-2026-05-15` snapshot branches). Both repos now have `main` as the only remote branch.
- Documentation cleanup (commit `ad70a2d`). Pruned `docs/safety-pack-migration-recipe.md` (recipe complete via Days 32-34 commits), `docs/tier-enforcement-flags.md` (rollout shipped, working-notes doc no longer needed), `docs/implementation-history.md` (overlapped DEVLOG without adding granularity), `docs/internal/next_steps_and_final_eval_plan.md` (first line said "not part of public README story" yet was tracked publicly), `docs/history/RAG_Experiment_Plan.md` (pre-production historic), `docs/history/README.md`. Promoted `DEVLOG.md` out of `docs/history/` to `docs/` since it's actively maintained. The original prototype `HelpmateAI_RAG_project_Cleaned.ipynb` stays on disk but is now gitignored — kept as portfolio artifact but no longer ships in the public repo. Stale `.gitignore` entries cleaned up.

Status: production fully recovered, both APIs healthy, no scheduled OpenAI cost surfaces remaining. Both repos have parity on observability and a clean docs surface.

Why now:

- one daily LLM-cost surface (\$45-90/mo nightly_eval) is hard to justify pre-revenue when the regression detection it provides catches drift only after a model upgrade has already shipped
- runtime-only Caddy edits silently delete on every restart — committing the config to git makes it survive the next docker recreate
- branch and doc clutter accumulates fast in an autonomous-shipping cadence; periodic pruning keeps the repo legible for both future-me and external readers

Challenges:

- the docker compose project-name semantics are non-obvious — the default project is the directory name (`vps`), the override file's `name:` field isn't always set, and a wrong `-p` flag silently mounts fresh volumes instead of failing loudly
- runtime Caddy state (via admin API or in-container edit) doesn't show up in any committed config; spotting drift requires diffing the autosave JSON against the Caddyfile, which isn't a routine ops check
- pre-revenue cost gating is a different calculus than mature-product cost gating — "useful safety net" can flip to "expensive false alarm" if the underlying traffic to defend doesn't exist yet

Improvements:

- the cron crontab now carries an in-place documentation block describing how to re-enable nightly_eval and the cost trade-offs, so future-me doesn't have to dig through DEVLOG to remember the rationale
- Caddy state for both Job Agent and HelpmateAI is in git, so the next Caddy restart can never again silently drop the Job Agent's reverse proxy
- the operations runbook (`docs/deployment.md`) now documents the docker-compose project-name gotcha + the volume-audit recovery pattern, so the same mistake doesn't cost the next operator an hour

ADRs added:

- [ADR-018: Observability stack — Sentry + PostHog with consent-gated analytics](adr/ADR-018-observability-stack-sentry-and-posthog.md)
- [ADR-019: EU cookie consent banner + GDPR-aligned analytics gating](adr/ADR-019-eu-cookie-consent-banner-and-gdpr-analytics-gating.md)
- [ADR-020: Manual-only nightly_eval at pre-revenue stage](adr/ADR-020-manual-only-nightly-eval-at-pre-revenue-stage.md)

## Day 35: Sentry + PostHog Observability Stack End-To-End

- Wired Sentry (errors, traces, AI Agents Monitoring, Logs, Replay, Crons, Feedback widget, profiling) + PostHog (product analytics, session replay, identify, group cohorts, LLM Analytics via `$ai_generation` events) into both backend and frontend. Eight HelpmateAI commits (`a8b0204` → `aba4184`) shipped the observability layer + cookie consent banner over a single day. The Crons monitor for `nightly_eval` was added here; the heartbeat schedule is documented in `docs/deployment.md` and later disabled on Day 36.
- **Backend wiring** (`backend/observability.py`): single bootstrap module that initializes both clients at import time. No-op when DSN / API key is empty (so dev + CI run unchanged). Sentry init includes `FastApiIntegration`, `StarletteIntegration`, `LoggingIntegration`, `OpenAIIntegration` (auto-spans for every LLM call with token + cost + latency), and `enable_logs=True` for the new Sentry Logs product. `before_send` filter drops intentional `HTTPException` events (4xx flow control + 5xx "not configured" / "temporarily unavailable" guards) so the issue feed stays focused on real bugs. `_running_under_pytest()` guard skips Sentry init when `PYTEST_CURRENT_TEST` is set, so `uv run pytest` against a real DSN no longer fires test fixtures into the production project (that bug burned ~50 events the first time the integration shipped — HELPMATE-BACKEND-2 through 7, all resolved + filtered after).
- **Frontend wiring** (`instrumentation.ts`, `instrumentation-client.ts`, `sentry.server.config.ts`, `sentry.edge.config.ts`, `posthog-provider.tsx`, `cookie-consent.tsx`): `@sentry/nextjs@^10.53` for Next 16 compatibility. Pageview tracking via App-Router `usePathname`+`useSearchParams` listener — the SDK's built-in `capture_pageview` doesn't fire on SPA navigation and would leave Web Analytics empty. `posthog.identify(user.id, traits)` + `posthog.group('tier', tier)` from `app-workspace.tsx` so dashboards can slice by user + plan tier. `Sentry.feedbackIntegration` injects a floating "Report an issue" button. `Sentry.replayIntegration` masks all text + blocks all media (PII-safe).
- **PostHog free-tier strategy.** PostHog free Developer plan caps at 1 project per org, so HelpmateAI and AI Job Agent share the same project. Every event gets a `product: "helpmate"` (or `"jobagent"`) super-property at SDK init via `posthog.register({product: "..."})` + a matching tag in backend `capture_event()`. Dashboards filter by `product = ...` for clean separation. The "Helpmate AI" project (179885, EU region) was renamed from "Default project" via the PostHog REST API after the Chrome MCP couldn't drive the React-controlled input. Free-tier maxed out: authorized URLs configured for prod + Vercel previews + localhost, recording domains allowlisted, heatmaps + surveys enabled, autocapture on, exception capture deliberately off (Sentry is the source of truth for errors).
- **Sentry-Vercel integration + source map upload.** Vercel marketplace integration installed for the HelpmateAI frontend (maps `helpmateai` Vercel project → `helpmate-frontend` Sentry project). The integration auto-provisions `SENTRY_AUTH_TOKEN` so `withSentryConfig` uploads source maps on every build — stack traces are now readable instead of minified. For Job Agent, the integration's save-step conflicted with already-set `NEXT_PUBLIC_SENTRY_DSN` env vars, so the manual fallback (env var + token set directly in Vercel) was used. ADR-018 documents both paths.
- **LLM Analytics via `$ai_generation` events.** Every LLM call inside a `/qa` request now emits a PostHog `$ai_generation` event with the documented property schema (`$ai_trace_id`, `$ai_span_name`, `$ai_provider`, `$ai_model`, `$ai_input_tokens`, `$ai_output_tokens`, `$ai_total_cost_usd`, `$ai_is_error`). The pipeline pulls per-call breakdown off `cost_collector.to_payload()` after the answer cache write so cache hits don't replay events. PostHog's LLM Analytics dashboard now shows per-span (router, planner, generator, support verifier, evidence selector) cost attribution per request.
- **EU cookie consent banner** (`frontend/src/components/cookie-consent.tsx`). Three-state machine in `localStorage["helpmate-cookie-consent"]`: `"pending"` (banner shown, no analytics), `"accepted"` (PostHog + Sentry Replay live), `"declined"` (PostHog opt-out, Sentry stays errors-only as legitimate interest under GDPR Art. 6(1)(f)). Footer + auth-sidebar both expose "Cookie preferences" links that reset to pending. Cross-tab `storage` event listener keeps state synced. Built in-house (~100 lines + theme CSS) rather than installing Cookiebot/Iubenda — those would have cost \$11-27/mo for compliance theater we don't need. ADR-019 captures the decision.
- **Smoke test** end-to-end: `curl https://api.helpmateai.xyz/health/sentry-debug` → HTTP 500 → `HELPMATE-BACKEND-1 "ZeroDivisionError"` in Sentry within seconds, with stack trace at `backend.main.sentry_debug`. Same pattern verified on Job Agent's `JOBAGENT-BACKEND-1`. GitHub integration + code mappings live so Sentry frames deep-link to GitHub source lines. Uptime monitor configured on `/health` for each backend (5-min interval, 3-failure threshold).

Status: live on both products. The error issue feed is clean (only smoke-test events). Tracing populated within hours. PostHog session replay captures 100% of errored sessions. Source maps upload on every Vercel build for HelpmateAI.

Why now:

- the project was operating blind — Vercel auto-tags the frontend but the FastAPI backend had no first-class crash reporter, no LLM cost attribution, no user-cohort analytics
- the upcoming payment cutover means we need real cohort behavior (free vs pro) and LLM cost-per-tier data on the dashboard the day a paid user signs up, not retrofit after
- both products are launching close together; sharing observability infra (single PostHog project, paired Sentry projects under one org) keeps the dashboard real estate sane

Challenges:

- PostHog's React-controlled inputs reject Chrome MCP's `form_input` + synthetic keyboard events because the SDK's onChange listener doesn't fire from JS-set values; the project rename had to go through `PATCH /api/projects/<id>/` from inside the authenticated browser tab
- Sentry blocks Chrome MCP execution on `*.sentry.io` entirely (no navigate, no JS exec, no click); the workflow had to either route through Sentry's REST API with a personal token or ask the user to click manually
- the Sentry-Vercel integration's env-var-upsert step fails on conflict with pre-existing manually-added env vars; the fallback (manual setup of `SENTRY_AUTH_TOKEN` + `NEXT_PUBLIC_SENTRY_DSN`) gives the same source-map upload behavior without the integration UI cooperating
- the pytest-skip guard was an emergency add after the first deploy fired 50+ test events into the production Sentry project within an hour — a useful failure mode to document

Improvements:

- both products now have full-stack observability (errors + traces + AI spans + logs + replay + crons + feedback) on free tier
- PostHog LLM Analytics shows per-span cost attribution that makes "which agent is the expensive one" a one-glance question on the dashboard instead of a half-hour Supabase query
- the cookie banner makes the EU compliance posture explicit and gives a user-visible privacy control instead of relying on `respect_dnt` which only ~3% of users toggle
- the operations runbook in `docs/deployment.md` now lists all observability env vars + their defaults + the rollback path (delete the env var, the SDK no-ops, no other code change needed)

## Day 34: UX Pack Wave 2 + Safety Pack Call-Site Migration + CI Hardening

- Merged PR #5 (commit `249c383`): UX Pack Wave 2 — voice input on the Ask textarea, thumbs-up/down feedback buttons with comment capture, and the prompt registry pattern (`prompts/<name>/v<N>.json` loaded by `backend/prompt_registry.py`). The registry decouples LLM prompts from Python f-strings so a prompt change doesn't need a code redeploy; 9 LLM agents now load their system prompts from JSON files including the answer generator, query router, support verifier, evidence selector, structure repair, document classifier, document landmarks, chunk semantics, and synopsis semantics. The voice input uses the browser `MediaRecorder` API + a `/transcribe` route backed by OpenAI Whisper.
- Merged PR #6 (commit `a9245d5`): Safety Pack call-site migration. The Production Safety Pack's three components (schema-strict outputs via Pydantic + `run_structured_prompt`, atomic cost tracking via `CostCollector`, schema-strict path for the answer generator and query router) all landed end-to-end after the wiring step was completed across the active `src/` call sites. Tests: 393 passed, no regressions.
- Added `.coderabbit.yaml` to enable CodeRabbit manual reviews on non-default base branches (commit `d692a0f`). The default config only reviews `main`-targeted PRs; the new config unblocks review on staging branches the dev cycle actually uses.
- CI hardening (commit `7a4a83e`): added a `uv lock --check` step that fails fast when `pyproject.toml` was edited without a corresponding `uv lock` regeneration. The production Dockerfile uses `uv sync --frozen` which installs strictly from `uv.lock`, so drift between `pyproject.toml` (new dep added) and `uv.lock` (still old) silently ships a broken image — caught on AI Job Agent when `python-multipart` was added to `pyproject.toml` but the lockfile wasn't regenerated, CI tests passed, but the prod container crash-looped on `import backend.routers.workspace` because FastAPI's multipart-form decorator path raised at module load. `uv lock --check` exits non-zero if the lockfile would change, blocking the PR before merge.
- Vercel TS error fix (commit `94da526`): explicit type annotation on `auth.getUser()` response in `landing/pricing.tsx`. The implicit `any` was getting caught by Vercel's stricter TS check on production builds even though local `next build` passed.

Status: PR #5 (Wave 2) and PR #6 (Safety Pack) both merged to `main` and deployed.

Why now:

- the prompt registry was the last piece of the Schema-Strict + Cost-Tracking + Registry-Loading trifecta from the Production Safety Pack (Day 31); without it, model and prompt iteration still required a code redeploy
- the CodeRabbit unblock matters during a heavy-iteration cycle where most PRs target temporary staging branches rather than `main` directly
- the `uv lock --check` CI gate is the kind of fix that's invisible when it works but catastrophic when it doesn't; the AI Job Agent crash-loop was a useful forcing function

Challenges:

- the voice input had to handle the OS-mic indicator turning off on early termination paths (recording cancelled, transcribe network failure, component unmount mid-recording) — without explicit `track.stop()` calls in every exit path, the OS mic icon stays on for hours
- the feedback button "click → server-side write → optimistic UI" loop is racey if the user clicks twice fast; the re-entry guard via `committingRef` was added after CodeRabbit flagged it on round 5 of bot review

Improvements:

- prompts are now data, not code — a model upgrade can update the system prompt by writing a new `v2.json` file without touching Python
- the safety pack's three components are live across the entire active LLM surface
- both repos now have the `uv lock --check` CI gate, so the lockfile drift class of bug is closed for new dependencies

## Day 33: Lemon Squeezy Subscription Scaffold

- Added `subscriptions` + `subscription_webhook_log` Supabase tables, owned + RLS-protected per `user_id`, with a `processor` column reserved so a future Stripe or Razorpay row can sit in the same shape (see `docs/sql/supabase-subscriptions.sql`).
- Wrote `backend/subscriptions.py` with a `get_active_subscription(user_id)` reader keyed by an LRU cache that expires on the calendar-minute bucket — `resolve_user_tier` sits on the hot path of `/qa`, `/documents/upload`, and `/workspace/quota`, so a Supabase round-trip per gate would shred P95. The minute-bucket TTL means a fresh subscription is visible within at most 60 seconds without the webhook having to invalidate anything (it does invalidate anyway for a sharper cutover).
- Wired `backend/webhooks/lemonsqueezy.py` to verify LS HMAC-SHA256 signatures with `hmac.compare_digest`, parse the event envelope, idempotency-check via `subscription_webhook_log`, and map each event to a status: `subscription_created` / `_updated` / `_resumed` / `_unpaused` / `_payment_success` / `_payment_recovered` → `active`; `_cancelled` → `cancelled` (tier retained until `current_period_end`); `_expired` → `expired`; `_paused` → `paused`; `_payment_failed` → `past_due` (tier retained during dunning). Unknown events log + return 200 so LS doesn't retry.
- `resolve_user_tier` now consults the table: paid tier returned only when `status ∈ {active, cancelled, past_due}` AND `current_period_end > now`. Defensive `_PAID_TIERS` whitelist ensures a future migration that adds an unknown tier doesn't `KeyError` at gate-check time.
- Frontend pricing CTAs in `landing-page.tsx` route to LS hosted checkout with `?checkout[custom][user_id]=<supabase uid>` so the webhook can attribute the resulting subscription to the right user. When `NEXT_PUBLIC_LEMONSQUEEZY_*` env vars are absent, the Pro CTA renders "Coming soon" and Business falls back to the existing `mailto:` — the branch is shippable into `main` before LS KYC clears.
- Documented setup, event mapping, idempotency model, and the local-webhook-signing recipe in `docs/lemon-squeezy.md`. `.env.example` ships the seven new env vars (5 backend + 2 frontend variant IDs + the upgrade URL).

Deferred to a follow-up PR once LS KYC and live variant IDs are in place:

- "Manage Subscription" button on the workspace shell (paid tiers only) that hits `POST /billing/portal` to mint a customer-portal URL. The route exists; the UI affordance is staged behind the same env-gating pattern as the checkout CTA.
- Post-checkout quota refresh — after LS redirects back from hosted checkout, the workspace should call `/workspace/quota` proactively so the user sees their new `pro` tier without a full reload. The hook point is the same `refreshQuota` callback already used by `/qa` success paths.
- A "you're on Pro now" toast on the first `/workspace/quota` response that flips from `free` to a paid tier, so the post-checkout transition is acknowledged inline.

Status: ready to ship behind env flags. The scaffold sits on `feat/lemonsqueezy-integration` and merges cleanly into `main` because both the backend (503 when secret missing) and the frontend (Coming soon CTA) gracefully degrade in the absence of LS credentials. The architectural rationale for picking LS (Merchant of Record vs Stripe/Razorpay direct, processor-neutral schema, migration path) is captured in ADR-017.

## Day 32: Tier Enforcement End-To-End

- Introduced a tier-resolution shim at `backend/tiers.py::resolve_user_tier(user)` plus a `TIER_LIMITS` matrix keyed by `Tier = Literal["free", "pro", "business"]`. The matrix is the single source of truth for doc cap, file-size cap, monthly question quota, premium answer quota, retention window, and default + premium model names (see commit `97be986`). Every paid surface routes through this shim — the payment integration on Day 33 only had to change `resolve_user_tier`, not the gate call sites.
- Added upload quota gates for file size and active-document count (commit `be34366`). `check_file_size_cap` runs against `UploadFile.size` (the post-multipart-parse body size) rather than the `Content-Length` header so a user's 25 MB file at the 25 MB Free cap returns 200, not 413 from the multipart envelope overhead. Doc-count is enforced even though the current single-workspace model never reaches it — when multi-doc workspaces ship, the gate activates without further wiring.
- Wired monthly `/qa` question quota with an atomic increment RPC (commit `8c3036d`). The Supabase `increment_question_counter(p_user_id)` function does `INSERT ... ON CONFLICT DO UPDATE ... RETURNING` so two concurrent requests deterministically produce N+1 and N+2. Increment fires AFTER successful pipeline completion, so a failed `/qa` doesn't burn a question and the user can retry without a refund path.
- Made the answer model tier-aware (commit `1e65ef6`). `/qa` now passes `model_override=TIER_LIMITS[tier]["answer_model"]` into `generate_answer`, and the answer cache key now includes the model name so a Free `nano` answer can't be served back to a Pro user asking the same question. `settings.answer_model` stays as the fallback for eval scripts and unauthenticated contexts.
- Shipped premium answers and the `/workspace/quota` endpoint (commit `7f80076`). Pro and Business can opt-in per-question to `gpt-5.5` via a frontend toggle; premium calls increment BOTH counters per spec, with two distinct 402 codes (`premium_unavailable` for Free, `premium_quota_exhausted` for Pro/Business at cap) so the toast can branch copy. The toggle resets after each submission — premium is per-question, not sticky.
- Replaced the workspace TTL sweeper with a tier-aware Python implementation (commit `56b4896`). `_touch_document_workspace` now writes a tier-dependent `expires_at`; Business strips the field entirely (sentinel-free "never delete"). The sweeper routes deletions through `FileStorage` so Supabase Storage bucket objects get cleaned along with the Postgres rows — the previous SQL-only `pg_cron` sweep was a no-op for the bucket and would have leaked orphans.
- Caught a security gap during the rollout: the first iteration of the quota RPC migration revoked EXECUTE from `public` and `authenticated` but missed `anon` — Supabase grants `anon` EXECUTE on public-schema functions by default, so an unauthenticated caller with the public anon key could have invoked `increment_question_counter(p_user_id='<victim>')` to burn another user's quota. Closed in commit `9a1028e` (Supabase migration `revoke_anon_quota_rpcs`, applied `20260514154130`, and backported into `docs/sql/supabase-quota-counters.sql` so a fresh-DB redeploy is secure out of the box). All three of `public`, `authenticated`, and `anon` are now revoked; only `service_role` retains EXECUTE.
- Followed up with a docs cleanup (commit `daed0db`) that dropped the stale SQL-only retention sweeper section from `docs/retention.md`; the Python sweeper is now the single documented path.

Status: deployed to production. The 6 feature commits + 2 fixups are live on `app.helpmateai.xyz` and have been running clean against real traffic since the cutover.

Why now:

- the COGS math on unlimited `gpt-5.5` per anonymous user was not sustainable at any usage scale a portfolio-grade landing surface could realistically attract
- the v1 plan caps a free user at the same model envelope a serious eval run would burn, while paid tiers unlock the higher-cost premium answer path without giving any non-paying user a way to grind it
- shipping the gates as flagged code that all currently resolve to `free` lets the payment scaffold (Day 33) land as a one-function change in `resolve_user_tier` rather than a sprawling per-gate retrofit

Challenges:

- the atomic-RPC pattern was chosen over a Python read-modify-write because two `/qa` calls at cap-1 racing against a non-atomic store can both pre-check pass and both run pipeline, producing N+1 answers on a cap of N; the SQL upsert closes that window cleanly
- the anon EXECUTE gap is the kind of mistake that doesn't surface in dev (no anon traffic against your local dev instance) and would only have shown up against production traffic; the post-merge audit caught it before any real harm
- the sweeper had to delete bucket objects + DB rows in the right order — `pipeline.delete_workspace` runs FIRST (it may need local files during teardown), then `FileStorage` cleanup, so a Supabase bucket key isn't yanked out from under a local cache invalidation pass

Improvements:

- every paid surface is now gated through a single resolver, which means the Lemon Squeezy work on Day 33 was a four-PR scaffold rather than a per-gate retrofit
- the quota counter is atomic and tamper-proof at the RPC boundary; client-side calls fail with permission-denied regardless of whose UUID they pass
- the retention sweeper handles Supabase Storage cleanup natively, which closes the orphan-object race the SQL `pg_cron` pattern had against the bucket
- the design decisions are now documented in ADR-015 (tier shim) and ADR-016 (atomic quota increment + the anon EXECUTE hotfix) so the next person who looks at this stack in 6 months has the rationale, not just the code

## Day 31: Production Deploy And Read Mode Routing Fixes

- Cut the `helpmateai.xyz` apex over from a Framer-hosted marketing site to a single Next.js Vercel deployment that serves both the apex landing and `app.helpmateai.xyz` workspace through a host-based Next rewrite.
- Moved the host rewrite from the default `afterFiles` bucket to `beforeFiles`. The first production attempt served the workspace at the apex because `app/page.tsx` matched `/` before the host rule could redirect to `/landing`.
- Switched the rewrite source from a catch-all `:path*` to explicit per-route entries for `/` and `/privacy-policy` so static assets like `/_next/static/*` and `/favicon.ico` are not mangled.
- Rerouted the Read Mode PDF fetch and the Download Original buttons from a relative `/api/documents/{id}/file` path to the absolute `API_BASE_URL` prefix. The proxy chain through Vercel's edge to `api.helpmateai.xyz` was being challenged by Cloudflare's bot protection on data-center origins, so PDF.js was receiving the JS challenge page instead of a PDF stream.
- Verified the deployed surface against the Vercel MCP project record and confirmed all production aliases (apex, `www`, `app`, stable preview) resolve to the same deployment.
- Tightened the validation strip H2 and the body paragraph typography on the editorial claims and flow carousel to match the rest of the marketing surface.

Challenges:

- the first push hit a transient GHCR network timeout on the GH Actions runner that initially looked like a permissions failure; the retry on the next push succeeded without any settings change
- the Vercel-edge to Cloudflare bot challenge is not a code bug but a real production-only failure mode that does not appear in dev or preview deployments

Improvements:

- single-project deploy now serves the apex, `www`, and `app` subdomains in one build, with the host-based rewrite handling the routing distinction
- the Download Original buttons are documented as a known follow-up because `window.open` cannot attach a bearer token and will 401 until a `fetch + Blob + anchor[download]` refactor lands
- the silent-failure path in the PDF viewer where `loadPdfjs()` rejected without setting a banner now surfaces a transport banner so an empty pane is no longer possible

## Day 30: Production-Grade Error Handling Pipeline

- Added a typed `ApiError` class with `status`, `detail`, `retriable`, and `retryAfterSeconds` thrown from the central fetch wrapper.
- Built a status × operation message map (`messageForApiError`) that returns `{title, body, action}` for every reasonable combination of operation (`upload`, `index`, `ask`, `load`, `default`) and status bucket (`0` offline, `401`/`403`, `404`, `408`/`504`, `429`, other `4xx` with backend detail, `5xx`).
- Mounted `sonner` in the root layout and themed it against the workspace tokens, with `notifyApiError(err, op, {onRetry})` rendering a retry button only when the error is retriable and a callback is supplied.
- Replaced the floating `ErrorBanner` with toasts for transient failures and a new inline `<ErrorState>` for the only persistent case, an index-failed workspace.
- Added `src/app/error.tsx` for route-level render errors and `src/app/global-error.tsx` for root-layout errors that need their own `<html>` and `<body>`.
- Plumbed retries via closures so each catch passes the original action back; ask retry captures the submitted question rather than the current textarea contents.
- Parsed `Retry-After` for `429` responses, including the HTTP-date format on top of integer seconds.
- Distinguished offline from unreachable server via `navigator.onLine` and a transparent passthrough for `AbortError` so request cancellation is not classified as a network failure.
- Surface workspace-restore failures through the same toast pipeline; the previous silent `catch {}` left users without a hint when their session failed to load.

Challenges:

- the first round of message copy was readable but generic; iterating on the 4xx upload fallback through the `design:ux-copy` skill gave a tighter "It may be too large, the wrong type, or damaged — try another PDF or DOCX."
- range requests issued by PDF.js during streaming bypass the loader's refresh-and-retry path; a token that expires mid-read surfaces as a generic transport error rather than the auth-specific banner

Improvements:

- every failure mode the workspace can hit now has a copy entry that names what the user can do rather than the HTTP status it received
- the new error boundary covers the previously catastrophic case where a React render error left a white screen with no recovery affordance

## Day 29: In-App Source Viewer With PDF.js And DOCX Rendition

- Added the `GET /documents/{id}/file` endpoint that serves the source PDF inline for the viewer and the original DOCX or PDF for download via `?download=1`. The endpoint is auth-gated by the same Supabase pattern as the rest of the API and supports HTTP `Range` requests so PDF.js can stream-render large files.
- Renamed uploads on disk to `{document_id}{ext}` so collision-safe storage holds for repeated uploads against the same workspace, and gave each document record a `viewable_pdf_path` field. PDF uploads alias their source as the viewable. DOCX uploads invoke `libreoffice --headless --convert-to pdf` at ingest to produce a sibling rendition, with the conversion failure path tolerated so a corrupted DOCX still indexes from text and only loses the inline viewer.
- Built Read Mode as a layout posture, not a per-citation modal. On desktop the workspace collapses to a chat-and-source two-pane shell with the doc strip and evidence rail hidden; on mobile the source becomes a `vaul` draggable bottom sheet with three snap points (`FULL`, `SPLIT`, `COMPACT`). `COMPACT` is reserved for keyboard-up state and is never reachable by drag.
- Wrapped `pdfjs-dist` with a lazy loader that copies the worker via a postinstall script and attaches a Supabase bearer token to the initial `getDocument` plus one-shot refresh-and-retry on `401`.
- Wrote the find pipeline as a hint-page + window strategy: parse the chunk's `pageLabel` as a fast-scroll hint, dispatch a `find` with the chunk's anchor prefix, and pick the match closest to the hint inside a `±3` page ring; fall back to a soft "Showing Page N" banner when nothing matches in the window.
- Auto-jumped the source viewer to the first evidence of every new answer while in Read Mode, and branched citation pill clicks so the same pill flashes the evidence card in normal mode and scrolls the viewer in Read Mode.
- Polished the chrome with a live page-pill that updates as the user scrolls, a centered filename, and a focal-glow close button focused on entry for keyboard users. Added page navigation (prev / next / editable page-pill) and a collapsible chat drawer so the PDF can take center stage on wide viewports.
- Tinted the find highlight in `--accent-soft` instead of PDF.js's default yellow, cleared a stale no-match banner on progressive find scans, and forwarded scroll-to-match through PDF.js's own viewport centering.

Challenges:

- `loadPdfjs()` originally caught its failures but never set a banner kind, so a bundler or worker failure produced a black pane with no message; the first round surfaced this as a silent error path that needed an explicit transport banner
- mobile drag-to-COMPACT had to be intercepted in the snap handler because `vaul` does not natively support snap-point exclusions
- the find pipeline can land on the wrong page when the chunker straddles a page boundary; the `±3` window with a strict fallback prevents misleading far-page jumps

Improvements:

- the verification loop is now visible end-to-end: pill flashes the evidence card; "Open in source" opens the same passage inside the PDF with the highlight already painted on the correct page
- the workspace ships with native PDF and DOCX both behaving identically from the viewer's perspective; the conversion step is invisible to the user
- the ±3 ring tolerates the page drift introduced by LibreOffice DOCX→PDF conversion while keeping search scoped to the chunk's neighborhood

## Day 28: Landing Page Launch With Atmospheric Editorial Design

- Replaced the Framer-hosted marketing site with a Next.js landing route group inside the same workspace project. A host-based rewrite in `next.config.ts` serves `/landing/*` only when the request host is `helpmateai.xyz`; `app.helpmateai.xyz` continues to serve the workspace at `/`.
- Designed the landing as a single dark canvas with teal aurora glow flanking the hero, no nested cards, and section breaks built from hairlines and breathing room rather than rounded panels. The aurora drift animation is gated behind `(min-width: 901px) and (prefers-reduced-motion: no-preference)`.
- Wrote the editorial claims as a sticky-pinned visual on desktop and inline cards on mobile, driven by an `IntersectionObserver`-backed `useActiveClaim` hook. The three claims cover the proof loop: shows the source, knows when to abstain, stays inside the document.
- Built the validation strip as the differentiator panel directly under the hero: `0%` false support, `100%` abstention on unanswerable, `94%` cited the correct paragraph, plus a three-row vendor comparison against OpenAI File Search and Vectara on the same 150-question suite.
- Added a four-step flow carousel (Upload → Understand → Find evidence → Cite answer) with grid-overlap cross-fades so tab switches never produce a layout jump. Step 04 uses a real Read Mode capture as its mockup so the verification posture is visible above the fold of that section.
- Added a privacy policy page inside the landing route group with the same dark-green chrome and justified body type so it reads as part of the marketing surface, not a generic legal scaffold.
- Did a copy pass that stripped RAG and UI jargon ("chunks", "evidence column rail", "interpretive layer", "answer states") in favor of plainer language while keeping the punchy editorial lines ("Citations, not citations-shaped marketing.", "It will not paraphrase the internet at you.") intact.
- Fixed a CTA contrast regression where `.l-shell a { color: inherit }` had higher specificity than `.l-cta { color: var(--accent-fg) }`, leaving the topbar and hero pill text near-white on green; scoped both selectors under `.l-shell` to bump specificity.

Challenges:

- the host rewrite was set in the default `afterFiles` bucket, which only runs after the file-system match — the workspace `app/page.tsx` matched `/` first and the rewrite never fired until it was moved to `beforeFiles` (see Day 31)
- the original carousel reused the same `workspace-flagship.png` for two consecutive steps because Read Mode didn't exist yet to generate a distinct visual; Step 04 now uses a dedicated Read Mode capture
- a Vaul `modal={false}` sheet positions itself through transforms and requires `position: fixed` on the sheet element, which was not set during initial scaffolding and put the sheet off-screen on first paint

Improvements:

- the marketing surface and the workspace now share the same design tokens (`--accent`, `--accent-fg`, `--accent-deep`, hairlines, motion), which means visual polish on one carries over to the other
- the validation panel is the headline differentiator panel, not a footnote, because the eval result is the only thing competitors cannot match by simply tuning their stack

## Day 27: Workspace UI Rebuild As A Three-Zone Document Study Room

- Rebuilt the workspace as a three-zone study room with a document strip on the left, the chat/answer column in the center, and the evidence rail on the right. The shell binds to `.h-shell` with a unified token system (`--bg-page`, `--bg-card`, `--accent`, `--accent-fg`, `--accent-deep`) that the landing will later inherit.
- Replaced template follow-up chips with LLM-generated starter questions per document, so the empty-state cues reflect the actual content rather than a hardcoded prompt list.
- Added an LLM-derived `support_summary` qualifier on every answer that explains in one sentence why support landed where it did (supported, partial, abstained). The qualifier is shown alongside the answer instead of replacing the explicit support state.
- Wired the per-turn actions menu (copy, cite, re-ask, delete) into each Q&A card so the conversation surface treats every turn as an addressable record.
- Tightened the ask card with a sticky position, an inline `ASK` label inside the textarea, and a `box-shadow` mask over older Q&A cards as the user scrolls so the current question always reads as the live surface.
- Trimmed the account popover to just the session row by dropping the Storage, Mode, and Auth tiles, since none of them were actionable for the current product stage.
- Hid the answer-status note for `SUPPORTED` answers because it was always the same boilerplate and bloated the card; partial and abstained still keep their notes.
- Wired the real Helpmate AI brand mark across the workspace and the new landing, with the topbar wordmark switched to the display font and the empty-state hero trimmed to a tighter tagline.
- Added a `framework_document` style classifier in the indexing layer with an LLM-backed fallback for documents that escape the deterministic classifier, and logged the chosen style at INFO so the indexing path is observable in production logs without grep-ing for traces.

Challenges:

- the previous workspace shell had picked up too much chrome (replay-stream button, follow-up chips, storage tile) over time; stripping it required walking each affordance and asking whether it earned its place
- the support summary needed to be visible without overwhelming the support state — it ends up as a one-sentence qualifier in `--fg-3` next to the state badge

Improvements:

- the three-zone layout makes the verification flow visible at a glance: the doc the user uploaded, the conversation in the center, and the cited evidence on the right — Read Mode (Day 29) is a natural extension of this posture
- the `support_summary` field gives downstream evals a clean signal about whether the answer model recognized the support boundary even when the verifier later corrected the state

## Day 26: Smart Indexing, Orchestrated Scope, And Ephemeral Run Traces

- Added generic section profiling at indexing time:
  - document section role
  - chapter number and chapter title
  - page range
  - reusable scope labels
- Added an LLM retrieval orchestrator that reads a compact document map and resolves explicit local scope to validated section IDs.
- Wired orchestration context into the reorder-only evidence selector so selection sees the same route, scope, and answer-focus metadata as retrieval.
- Added hard-scope enforcement after reranking so scoped questions cannot drift into globally attractive but irrelevant chapters.
- Added ephemeral run traces for uncached QA runs, stored locally or in Supabase with the same workspace retention window.
- Reviewed the older `hybrid-indexing-docs-refresh` branch and carried forward the durable part:
  - policy-aware canonical headings and aliases
  - policy-aware structure-repair triggers
  - policy-aware synopsis semantic gating instead of blanket-skipping policy documents

Challenges:

- deterministic scope extraction was becoming too brittle for differently phrased questions
- the earlier hybrid-indexing branch contained useful ideas mixed with stale rollbacks, so it could not be merged wholesale
- workflow memory needed to help debugging without violating the one-day workspace retention model or storing full document text

Improvements:

- scoped retrieval eval improved from `0.00` full scope compliance without orchestration to `1.00` with orchestration, with `1.00` chapter-scope hit after low-value front matter was filtered out of hard scope
- full-stack snapshot on the branch recorded retrieval objective `0.6274`, answer supported rate `0.8158`, RAGAS faithfulness `0.8173`, and context precision `0.6560`
- lean upgrade RAGAS on six targeted cases recorded `1.0000` supported rate, `0.9050` faithfulness, `0.6034` answer relevancy, and `0.7500` context precision
- lean regression against `main` on five shared cases stayed at `1.0000` supported rate and improved faithfulness by `+0.0110`, answer relevancy by `+0.0966`, and context precision by `+0.1000`
- lean vendor comparison on the same six cases showed Helpmate at `6/6` supported answers versus `4/6` for OpenAI File Search and `4/6` for Vectara, with Helpmate leading answer relevancy and context precision
- run-trace eval confirmed traces are saved, preview-limited, expire with the workspace, and do not copy the full answer body or full document text
- the skipped hybrid-indexing branch is now represented by targeted code and tests rather than being lost

## Day 25: Support Guardrails Stopped Treating Partial Evidence As Failure

- Investigated broad thesis questions that returned unsupported answers despite useful evidence being present.
- Added generalized section-name parsing for natural phrasing like "in the literature review what was..." without hard-coding thesis-specific section names.
- Changed final retrieval selection so heading-only chunks defer to their body continuation when available.
- Swept weak/unsupported evidence thresholds and found threshold-only tuning was not a defensible fix.
- Added `src.evals.support_guardrail_eval` to evaluate:
  - labeled calibration positives and negatives
  - held-out manual questions in `static/sample_files/test`
  - retrieval status versus final answer support
- Updated the generation prompt to allow grounded partial answers while still abstaining when no substantive answer is supported.

Challenges:

- retrieval scores alone could not separate negatives from positives because broad off-topic questions still retrieved high-scoring domain-adjacent chunks
- the first instinct, lowering or raising weak/unsupported thresholds, would have overfit the wrong layer
- broad user questions often ask for more than the top evidence window can fully cover, so "all-or-nothing" support was too brittle

Improvements:

- held-out manual test-folder questions reached `1.0000` answer-supported rate after the prompt contract change
- calibration negatives stayed at `1.0000` abstention with `0.0000` false support
- the repo now has an ADR and repeatable eval report for why thresholds were left unchanged

## Day 24: Final Vendor Rerun On The Stabilized Stack

- Reran the external `ragas` benchmark suite against both:
  - OpenAI File Search
  - Vectara retrieval
- Fixed the local `ragas` harness so selector-enabled generation is evaluated the same way the live pipeline behaves.
- Updated the published benchmark summary to the new `2026-04-19` stabilized snapshot.

Challenges:

- the external comparisons are expensive enough that they only make sense after the internal defaults settle
- the local answer-quality harness had quietly drifted from the live selector-enabled path and needed correction before the final rerun was trustworthy

Improvements:

- the repo now has a fresh external benchmark snapshot on the current shipping architecture
- Helpmate now leads Vectara by `+0.1997 / +0.1350 / +0.1523` and OpenAI File Search by `+0.4532 / +0.4021 / +0.3697` on average for faithfulness / answer relevancy / context precision across the four main document families

## Day 23: Deployment, Auth, Retention, And Cleanup Finally Matched The Retrieval Core

- Finished the `Next.js + FastAPI` product path.
- Deployed:
  - `app.helpmateai.xyz` on Vercel
  - `api.helpmateai.xyz` on a VPS behind Caddy
- Added Google/Supabase sign-in.
- Added one-active-document-per-user with resumable `24h` sliding retention.
- Added VPS-side cleanup for:
  - uploads
  - local indexes
  - stale answer-cache files
- Switched larger browser uploads to the direct API path to avoid Vercel request-size limits.

Challenges:

- the product shell had fallen behind the maturity of the retrieval stack
- retention needed to clean both database state and local disk artifacts, even when users never returned
- browser uploads through the Vercel proxy broke on larger files

Improvements:

- the live product now behaves like the architecture we benchmark
- large uploads no longer depend on Vercel's body-size ceiling
- workspace resume and expiry are now real product behavior, not just a backend concept

## Day 22: Selector Calibration Closed The Loop On The Earlier Architecture Doubt

- Completed reorder-only selector follow-up sweeps for:
  - weight blend
  - gap threshold
  - trigger-source policy
- Promoted the selector back into the default stack in reorder-only mode.
- Set production selector policy to:
  - `spread-only`
  - no ambiguity trigger
  - no weak-evidence-only trigger

Challenges:

- the selector had already been disabled once, so re-enabling it needed stronger evidence than a single win
- always-on selection improved grounding, but we needed a cleaner production tradeoff than "best metric at any cost"

Improvements:

- reorder-only selector is now a benchmarked default rather than an unresolved experiment
- the selector only activates where it helps most, instead of behaving like a universal second-stage answer tax

## Day 21: Chunking, Reranker Model Choice, And Default Retrieval Settings Became Measured Choices

- Ran the first real chunking sweep and answer-layer follow-up.
- Promoted chunk overlap from `180` to `240`.
- Compared reranker models and kept `cross-encoder/ms-marco-MiniLM-L-6-v2`.
- Completed retrieval-default sweeps for:
  - synopsis section window
  - synopsis top-k pool
  - global fallback pool
  - planner candidate region limit
- Completed repair-threshold and topology-edge sweeps.

Challenges:

- several important defaults had grown from "reasonable" intuition rather than direct measurement
- the first chunking experiment exposed an index-reuse bug, so the evaluation harness itself had to be corrected before the result could be trusted

Improvements:

- the stack defaults now reflect measured tradeoffs instead of inherited settings
- the project gained a much stronger documentation and interview story around benchmark discipline

## Day 14: Architecture Ablations, Threshold Calibration, And Stack Scorecard

- Added a layered architecture-eval workflow rather than relying on a single benchmark.
- Added and saved:
  - selector weight sweep
  - selector on/off ablation
  - reranker on/off ablation
  - planner/router threshold sweep
  - planner ablation
  - answer-stack ablation
  - latency/cost benchmark
  - focused `ragas` stack comparison
- Added a compact architecture scorecard and roadmap under `docs/evals/`.
- Recorded the benchmark-driven architecture decision in a new ADR.

Challenges:

- some of the most plausible layers were the hardest to justify cleanly because they improved one signal while hurting another
- planner usefulness could not be judged fairly until its heuristic confidence thresholds were calibrated
- selector tuning risked looking persuasive on its own even though the real question was whether the selector should remain in the default stack at all

Improvements:

- reranker is now strongly justified with both retrieval-level and answer-level evidence
- planner/router is now calibrated and documented as a modest positive rather than a dramatic win
- evidence selector is now documented as experimental instead of implicitly assumed to be part of the best default architecture
- Helpmate now has a proper evidence trail for why each major layer stays or remains under review

## Day 15: Selector Pruning Bug Isolated And Overturned

- Revisited the selector conclusion from the earlier stack ablations.
- Traced the selector path and confirmed the old implementation was not just reranking evidence:
  - it was pruning the final answer context down to a bounded shortlist
- Added a reorder-only selector mode controlled by:
  - `HELPMATE_EVIDENCE_SELECTOR_ENABLED`
  - `HELPMATE_EVIDENCE_SELECTOR_PRUNE`
- Added matched comparison harnesses for:
  - retrieval-only selector-off vs prune vs reorder-only
  - answer-layer selector-off vs prune vs reorder-only
  - focused `ragas` selector-off vs prune vs reorder-only
- Recorded the architecture update in a new ADR.

Challenges:

- the earlier selector verdict was correct for the code we had, but it was not isolating the true variable
- the first selector-specific `ragas` rerun also had a harness issue and had to be discarded before we could trust the final result
- the selector gate turned out to fire on most benchmark questions, so any production recommendation had to accept a real latency tradeoff rather than a tiny edge-case cost

Improvements:

- proved the regression came from pruning, not from evidence reordering itself
- reorder-only selector outperformed both selector-off and prune mode on the matched retrieval and answer-layer comparisons
- focused `ragas` also flipped in favor of reorder-only, with stronger faithfulness and context precision than the planner+rereanker baseline
- selector is now promoted back into the default stack, but only in reorder-only mode

## Day 11: Document-Topology Retrieval Upgrade

- Added a deterministic `RetrievalPlan` layer ahead of retrieval.
- Added lightweight topology artifacts:
  - section synopses
  - topology edges
  - generic region kinds
- Added a dedicated synopsis collection alongside chunk and section collections in local Chroma storage.
- Reworked retrieval to support:
  - `chunk_first`
  - `synopsis_first`
  - soft local structural guidance
  - soft multi-region retrieval with global fallback
  - hard-region behavior only for explicit page, clause, or named-section references
- Removed the active query rewriting layer from retrieval.
- Added structure-aware retrieval metrics:
  - `section_hit_rate`
  - `region_hit_rate`
  - `plan_accuracy`
  - `global_fallback_recovery_rate`
  - `multi_region_recall`

Challenges:

- planner mistakes can bias retrieval more than plain similarity-only search
- synopsis quality had to stay factual and lightweight rather than lossy
- distributed questions needed structural guidance without collapsing the current multi-page evidence behavior

Improvements:

- health-policy retrieval stayed stable through the upgrade
- `pancreas7` and `pancreas8` remained strong under the new retrieval flow
- thesis retrieval became more inspectable, with clearer planner and region metrics showing where future tuning is needed

## Day 12: Planner Recovery And Bounded Evidence Selection

- tightened query-shape handling for broad summary and specific implementation/detail questions
- improved topology region selection with early-vs-late summary bias
- added low-value synopsis suppression for bibliography/manuscript-style noise
- bumped the index schema to rebuild topology artifacts cleanly
- added a bounded post-rerank evidence selector:
  - only reviews top retrieved candidates
  - keeps a rank prior
  - can still promote lower-ranked but more direct evidence

Challenges:

- planner changes can easily help one broad-question family while hurting another
- broad paper-summary questions remain harder than clause-style or exact factual questions
- extra LLM help had to stay tightly bounded so latency and instability did not spread through the whole pipeline

Improvements:

- thesis recovered to a stronger retrieval snapshot than the earlier pre-topology baseline
- health stayed stable
- `pancreas7` kept its gain
- the evidence selector now fixes some cases where the correct evidence was already in top `k` but not rank 1

## Day 13: Low-Confidence Structure Repair And Dedicated Global Summaries

- Added a low-confidence structure-repair layer at indexing time for noisy journal PDFs.
- Kept deterministic section extraction first and only invoked a small model on suspicious documents.
- Added retrieval and negative eval datasets for:
  - `reportgeneration`
  - `reportgeneration2`
- Added a dedicated `global_summary_first` route for broad paper-summary questions.
- Added summary-aware prompt shaping for the final answer model without changing factual-answer behavior.

Challenges:

- some journal-style papers flattened section structure badly enough that synopsis/topology retrieval inherited the wrong document map
- broad questions like `What is this paper about?` were still failing even when relevant chunks were already in the candidate set
- the new summary improvements had to avoid harming the benchmarked policy, thesis, and pancreas document families

Improvements:

- indexing-time structure repair improved section quality only where needed and kept extra model cost out of the live query path
- `reportgeneration` broad-summary behavior improved materially
- `reportgeneration2` main-contribution behavior recovered
- the four older benchmark docs stayed stable or slightly better after the summary-route work

## Day 1: Notebook-To-App Restructure

- Refactored the repository from a notebook-first layout into a real app structure.
- Added:
  - `app.py`
  - `src/`
  - `tests/`
  - `docs/`
  - Docker and Render scaffolding
- Standardized dependency management on `pyproject.toml` and `uv.lock`.
- Kept the original notebook as a reference artifact rather than the main implementation surface.

Challenges:

- the original notebook mixed ingestion, retrieval, generation, and experimentation in one place
- the repo looked like a demo rather than a deployable product
- dependency management was not aligned with sibling projects

Improvements:

- moved core logic into reusable modules
- made Streamlit a thin UI shell instead of the business-logic home
- aligned the repo with the same project shape used successfully in sibling apps

## Day 2: Local-First RAG Baseline

- Implemented PDF and DOCX ingestion.
- Added deterministic chunking and Chroma-backed persistent indexes.
- Added hybrid retrieval:
  - dense retrieval
  - TF-IDF lexical retrieval
  - fusion
  - reranking hook
- Added answer caching and citation-aware answer generation.

Challenges:

- the first pass was structurally sound but not yet quality-controlled
- long-document retrieval quality was hard to judge from manual spot checks alone
- policy-style questions needed exact-term retrieval support, not only embeddings

Improvements:

- hybrid retrieval gave the system better exact-term behavior
- persisted indexes made repeated testing practical
- typed pipeline boundaries made later tuning easier

## Day 3: Product Shell, Deployment, and Tests

- Added the Streamlit UI with the same visual language as the AI Job Application Agent.
- Added:
  - `.streamlit/config.toml`
  - Dockerfile
  - Render manifest
  - CI
  - initial focused tests

Challenges:

- the repo needed to become presentation-ready, not just technically runnable
- deployment and local dev setup needed to feel consistent with the other portfolio projects

Improvements:

- created a clean app shell that can be demoed and deployed
- verified core helpers with tests instead of relying only on manual runs

## Day 4: Retrieval Quality Controls

- Added:
  - retrieval eval dataset
  - negative abstention eval dataset
  - OpenAI file-search benchmark harness
  - saved benchmark reports
- Started measuring local RAG against a hosted retrieval baseline.

Challenges:

- early retrieval quality looked weaker than expected
- some supposed retrieval failures were actually problems in the eval labels
- without saved benchmark reports, improvements were too easy to overclaim

Improvements:

- benchmark labels were corrected to match the real document contents
- comparison against OpenAI gave us a meaningful external baseline
- benchmark reports became part of repo history under `docs/evals/reports/`

## Day 5: Query Rewriting, Metadata-Aware Retrieval, and Adaptive Retry

- Added query rewriting fallback.
- Added page-aware retrieval filters and heading-aware ranking.
- Added adaptive re-retrieval when evidence is weak.
- Improved citation rendering and retrieval transparency in the UI.

Challenges:

- policy documents often mix exact clauses, definitions, and operational sections
- weak evidence needed to trigger retrieval recovery rather than immediately flowing into answer generation
- retrieval needed more transparency to debug what was actually happening

Improvements:

- retrieval became more inspectable
- page-specific and heading-specific questions became more reliable
- the system could now retry with better query variants when the first pass was weak

## Day 6: Structured Abstention

- Moved answer generation to a stricter structured output contract.
- Added explicit `supported` status to answer results.
- Updated the UI to show supported versus unsupported answers.
- Updated the negative eval to judge abstention using the typed output rather than fuzzy wording.

Challenges:

- unsupported questions could still produce vague answers
- string-based abstention checks were brittle

Improvements:

- unsupported questions now fail more honestly
- abstention became measurable and testable

## Day 7: Health-Policy Benchmark Generalization Pass

- Added a new benchmark set for the health insurance wording document.
- Compared local retrieval and OpenAI hosted retrieval on a second policy document family.

Challenges:

- Chroma emitted repeated telemetry warnings that looked like hangs even when runs completed
- the health-policy PDF was encrypted and needed an extra dependency for reliable parsing
- some retrieval misses were true clause-level misses rather than eval mistakes

Improvements:

- added `cryptography` to support encrypted PDF handling
- confirmed the architecture generalized beyond the first policy sample
- identified that the remaining weakness was not the app shell, but clause-level retrieval precision

## Day 8: Base Snapshot And Safe Rollback Point

- Committed and pushed the benchmarked baseline to `main` before further architectural changes.

Challenges:

- we needed a safe fallback before introducing a smarter retrieval layer
- local config and ad hoc benchmark files needed to stay out of git

Improvements:

- updated `.gitignore`
- created a clean rollback point before changing retrieval architecture further

## Day 9: Document-Intelligence Layer

- Added:
  - `src/structure/`
  - `src/query_analysis/`
- Enriched ingestion with:
  - section headings
  - clause ids
  - section paths
  - content types
- Upgraded chunking to preserve semantic structure metadata.
- Updated retrieval to use query classification and soft structural preferences during ranking.

Challenges:

- repeated document-specific tuning risked overfitting to one PDF family
- policy-style improvements did not necessarily transfer to thesis-style or narrative documents
- richer metadata introduced storage constraints in Chroma

Improvements:

- moved from ad hoc document-specific boosts toward structure-aware retrieval
- added a more portable middle layer between raw text and answer generation
- positioned the system for future hierarchical retrieval

## Day 10: Thesis Benchmark And Portability Learnings

- Added thesis-specific positive and negative benchmark datasets.
- Benchmarked the upgraded pipeline on a very different document style: a long academic thesis.
- Fixed Chroma metadata sanitization for list-valued fields produced by the new structure layer.

Challenges:

- Windows terminal output failed on some Unicode dissertation symbols during inspection
- Chroma rejected list-valued metadata such as `section_path`
- retrieval quality dropped on thesis-style narrative questions compared with policy documents

Improvements:

- sanitized Chroma metadata at the storage boundary while preserving structured metadata locally
- confirmed the system still generalizes beyond policy documents
- learned that the next major weakness is broader narrative and section-level synthesis, not just exact-clause retrieval

## Current Summary

Current system strengths:

- strong modular app architecture
- local-first inspectable RAG stack
- explicit abstention and benchmark discipline
- measurable outperformance versus hosted OpenAI retrieval on the current document-specific benchmarks
- first document-intelligence layer now in place

Current weaknesses:

- retrieval is much stronger on structured policy documents than on long academic prose
- section-level and cross-section narrative questions remain harder than factual clause lookups
- Chroma telemetry remains noisy in terminal output even though it does not block runs

## Day 11: Section-First Retrieval Layer

- Added section records and persisted section indexes.
- Added dual retrieval paths:
  - `chunk_first`
  - `section_first`
  - `hybrid_both`
- Added a lightweight query router to choose between those retrieval paths.
- Added focused tests for section building and query routing.

Challenges:

- we needed broader-question support without sacrificing the already strong factual benchmark
- replacing chunk retrieval entirely would have weakened clause-heavy policy questions
- older indexes on disk did not contain sections and needed safe rebuild behavior

Improvements:

- kept exact chunk-grounded retrieval as the primary factual path
- added section-first narrowing for synthesis-heavy questions
- made routing behavior visible in retrieval notes and benchmark outputs

## Day 12: Better Section Summaries And Academic-Document Handling

- Improved section construction for theses and research papers.
- Added:
  - canonical heading detection
  - cleaner section titles
  - better section summaries
  - suppression of common author-manuscript and reference-style noise
- Added section-kind aware ranking preferences such as:
  - `Abstract`
  - `Introduction`
  - `Results`
  - `Conclusion`
  - `Future Work`

Challenges:

- narrative documents do not behave like policy wording documents
- broad questions such as “main aim” or “future work” can be misclassified as factual lookups
- review papers often contain front matter and bibliography text that pollute section retrieval

Improvements:

- thesis benchmark improved from `0.75 / 0.5486` to `0.8333 / 0.5903`
- the pancreas8 review-paper benchmark improved from `0.8 / 0.75` to `0.9 / 0.85` at the best section-summary stage
- policy benchmark stability was preserved while narrative retrieval improved

## Day 13: Lightweight LLM Router Trial And Latency Check

- Added a lightweight LLM-assisted router as a tie-breaker for low-confidence mixed queries.
- Added timing instrumentation to measure router overhead in the live pipeline.

Challenges:

- heuristic routing still struggled on some broad paper-style questions
- it was unclear whether an LLM router would meaningfully help or just add latency

Improvements and learnings:

- the LLM router remained lightweight and bounded; it only selects a retrieval route
- it is not a full agent and does not answer questions itself
- latency impact is limited because it only runs on low-confidence cases
- on one broad paper-style query, the router added about `1.37s` but reduced total runtime by avoiding a heavier `hybrid_both` path
- on clean factual questions, the router is usually not the bottleneck

Current caution:

- the LLM router is useful as a tie-breaker, but it is not yet a guaranteed accuracy gain across every benchmark
- document parsing quality is still the stronger next lever than adding more routing complexity

## Day 14: Layered Evaluation With Ragas

- Added `ragas` as an open-source evaluation layer on top of the existing benchmark harness.
- Added:
  - `src/evals/ragas_eval.py`
  - `tests/test_ragas_eval.py`
  - `docs/evals/README.md`
- Updated the benchmark comparison runner so saved reports now include:
  - custom retrieval metrics
  - negative abstention metrics
  - OpenAI hosted retrieval baseline
  - `ragas` answer faithfulness
  - `ragas` answer relevancy
  - `ragas` no-reference context precision

Challenges:

- the new `ragas` version in this environment did not work cleanly with the newer factory pattern, even though that is the direction recommended in the docs
- the practical working bridge used LangChain wrappers, which are deprecated upstream but stable enough for the current repo
- our existing eval datasets are retrieval-labeled, not gold-answer datasets, so we had to start with no-reference answer-quality metrics rather than full reference-based scoring
- full comparison runs are now slower because they include extra LLM-evaluator passes

Improvements:

- we can now distinguish retrieval errors from answer-quality errors more clearly
- broad academic-paper questions are easier to diagnose because `ragas` exposes cases where retrieval is acceptable but answer relevance is weak
- benchmark reports are closer to a real evaluation matrix instead of a single score family

Latest concrete example:

- on `static/pancreas8.pdf`, the first combined report showed:
  - local retrieval: `0.8 / 0.8`
  - negative abstention: `1.0`
  - OpenAI file search: `0.4`
  - `ragas` faithfulness: `0.8429`
  - `ragas` answer relevancy: `0.4885`
  - `ragas` context precision: `0.8056`

Key takeaway:

- retrieval and grounding remain decent on the review paper
- the weaker area is answer relevance for broad paper-summary questions
- that gives us a much sharper picture of what to improve next

## Day 15: Vectara Benchmarking And Eval Policy Simplification

- Added Vectara as an external retrieval benchmark.
- Added a shared-answer comparison path so OpenAI and Vectara retrieval contexts could be judged under the same answer model.
- Captured answer-quality comparison across:
  - Helpmate
  - Vectara retrieval plus shared answer model
  - OpenAI retrieval plus shared answer model
- Added benchmark summary documentation under:
  - `docs/evals/benchmark_summary.md`

Challenges:

- Vectara factual-consistency scores were highly sensitive to our answer formatting and did not align well enough with the rest of the benchmark picture
- full vendor answer-eval runs are slow because they combine retrieval, answer generation, and multiple evaluator passes
- OpenAI File Search consistently lagged as an external baseline on the tested document families

Improvements and decisions:

- Vectara is now the primary external retrieval benchmark
- OpenAI File Search remains in the repo as a historical/reference retrieval baseline
- `ragas` is now the only answer-quality meter we use in routine benchmarking
- vendor factual-consistency APIs are no longer part of the active benchmark decision loop

## Day 16: Product-Surface Polish And Frontend Handoff Prep

- Improved the Streamlit app surface with:
  - document status panels
  - index status context
  - style-aware starter questions
  - benchmark snapshot tab
  - richer evidence presentation and retrieval debug details
- Rebased the project documentation around the current system state:
  - benchmarked RAG core
  - simplified eval policy
  - frontend as the next major phase

Challenges:

- the backend had become meaningfully stronger than the current UI presentation
- the app still felt more like a research shell than a portfolio-grade product
- several docs still reflected an earlier “Streamlit-first buildout” mindset instead of the current “backend strong, frontend next” state

Improvements:

- the app now does more onboarding and framing work for the user
- benchmark and document-state visibility moved into the product surface instead of living only in repo docs
- the docs now clearly reflect that Helpmate is in a strong enough technical place to invest in a proper frontend

## Day 17: FastAPI And Next.js Product Shell

- Added the first real `FastAPI` boundary in `backend/`.
- Started the custom product UI in `frontend/` with `Next.js`.
- Kept Streamlit available as the internal benchmark and inspection shell.

Challenges:

- the retrieval core had become stronger than the credibility of the original Streamlit-only surface
- the product needed a cleaner public-facing workflow without disturbing the benchmarked backend behavior
- the repo documentation still reflected an earlier Streamlit-first mindset

Improvements:

- the product now has a clear frontend/backend direction
- the Python retrieval core remains reusable instead of being tied to one UI shell
- the repo is better positioned for a more premium presentation layer

## Day 18: Removed LLM Query Rewriting And Added Retrieval Guardrails

- Removed model-based query rewriting from the retrieval path.
- Replaced it with deterministic weak-evidence expansion in `src/retrieval/query_rewriter.py`.
- Added evidence grading with:
  - `strong`
  - `weak`
  - `unsupported`
- Added direct retrieval guardrails so obviously irrelevant questions can fail before answer generation.

Challenges:

- model-based rewrite behavior was variable and made benchmark runs harder to interpret
- broad-question rewrites sometimes helped one document but hurt another
- irrelevant questions could still travel too far through the answer pipeline

Improvements:

- retrieval behavior is now simpler and more predictable
- off-topic questions can now fail fast with a clean guardrail response
- only the weak middle band triggers adaptive retrieval recovery

## Day 19: Stronger Section-First Retrieval Without More Model Layers

- Improved section summaries, aliases, and section-kind aware ranking.
- Added deterministic section seeding for summary-style queries such as:
  - `abstract`
  - `introduction`
  - `overview`
  - `discussion`
  - `conclusion`
  - `future work`
  - `recommendations`
- Added index schema versioning so section representation changes rebuild cleanly.

Challenges:

- broad thesis and paper questions still struggled even after the earlier structure-aware upgrade
- the next fix needed to generalize rather than overfit to one thesis or paper
- Windows/Chroma index reuse can become brittle when index layout changes under active files

Improvements:

- thesis future-work retrieval now resolves through the stronger section path
- `pancreas8` main-focus retrieval now resolves through the stronger section path
- old index layout changes now have a safer rebuild path via schema-versioned storage

## Day 20: Four-Document Benchmark Pass After Retrieval Simplification

- Ran the full benchmark stack again across:
  - health policy
  - thesis
  - `pancreas7`
  - `pancreas8`
- Updated the benchmark summary docs with the latest `ragas` comparisons for:
  - Helpmate
  - OpenAI retrieval + shared answer model
  - Vectara retrieval + shared answer model

Challenges:

- the full benchmark suite is slow because each document triggers multiple eval families and vendor calls
- long wrapper runs can outlive the local tool window even after the reports have already been written

Improvements:

- health-policy performance stayed stable after removing model-based rewrite logic
- `pancreas8` improved materially under the stronger section-first retrieval path
- thesis and `pancreas7` are now the clearest remaining retrieval-quality targets

## Day 21: Held-Out Final-Eval Suite And Support-Verifier Correction

- Promoted the 150-question held-out product-fit suite as the current public evaluation marker.
- Compared HelpmateAI against native OpenAI File Search and native Vectara runs instead of shared-answer vendor rows.
- Added partial-answer accounting so strict support and answerable coverage are reported separately.
- Fixed a support-verifier policy bug where a second-pass verifier could identify a fully supported atomic answer but the code still forced it back to unsupported.

Challenges:

- answerable coverage was close to vendor baselines, but atomic metadata lookups and table-heavy numeric questions exposed the remaining ingestion/retrieval weakness
- one arXiv footer question was incorrectly recovered from a bibliography entry, showing that document-identity metadata needs to be separated from references
- raw vendor supported rates are not enough by themselves because OpenAI and Vectara also produced false support on intentionally unsupported questions

Improvements:

- corrected HelpmateAI estimate: `92.6%` answerable coverage, `89.6%` strict fully supported rate, `7.4%` false abstention, `0.0%` false support, and `100.0%` unsupported-question abstention
- recovery now permits full support only when the verifier finds direct support for all required facts with no missing facts, no gap language, and no inferential phrasing
- next backend focus is artifact-aware ingestion for title pages, footers, forewords, acknowledgements, definitions, and tables rather than another answer-layer threshold change
