# ADR-023: Abstention-Robustness Posture After The Safety-Pack Regression

Date: 2026-05-17

Status: Shipped (local; behind the ADR-020 eval gate before push)

Refines the support-guardrail behaviour of [ADR-011](ADR-011-partial-grounded-answers-and-support-guardrail-eval.md) (partial answers + support-status verifier). ADR-011's intent stands; this records *why three specific knobs are set the way they are now* so the regression fix is not silently reverted.

## Context

A reported "default recommended question → ABSTAINED" on a freshly ingested FOMC document triggered a 5-6-day audit of every behavioural change in the window. The Safety Pack (PR#6, `a9245d5`, "call-site migration") had over-tightened the abstention layer in three coupled ways, and free-tier model routing amplified it:

1. **`verify_support_status` had no path back to "supported".** Once the answer acknowledged any gap (`answer_acknowledges_gap=True`), the supported→partial→unsupported cascade forced "unsupported" even when the verifier itself found grounded `supported_facts` and an **empty** `missing_or_ambiguous_facts`. A second defect downstream: even after the verifier returned "supported", `supported=True` was additionally gated on `not _answer_reports_support_gap(answer_text)`, so a polite caveat kept `supported=False` and `elif not supported` re-stamped "unsupported". Net: a fully-grounded, correctly-cited answer was abstained purely for hedging.
2. **`StructuredLLMModel` used `extra="forbid"`.** Any benign extra key the model volunteered raised `StructuredOutputError`, which the call sites route to a hard "Schema drift" → unsupported fallback.
3. **Free/default tier `answer_model` was `gpt-5.4-nano`.** Nano's looser JSON shaping and heavier hedging made (1) and (2) fire on the default path far more than on mini.

A future maintainer could plausibly *re-introduce each*: re-tighten the verifier "to be safe", re-add `extra="forbid"` to "match OpenAI strict mode", or revert nano→mini "to cut COGS". This ADR exists so those look like regressions, not improvements.

## Decision

The abstention posture is fixed at three points and recorded as deliberate:

1. **The verifier's evidence-grounded verdict wins over the answer's self-doubt.** When `verify_support_status` finds `supported_facts` present and `missing_or_ambiguous_facts` empty, the answer stays "supported" regardless of whether the answer text hedged. A genuine verifier-found gap still → partial (gap owned) / unsupported (unowned). The post-verifier promotion no longer gates on `_answer_reports_support_gap(answer_text)`; the inferential-language guard stays (a *weaselled* grounded claim still doesn't earn "supported"). An over-correction guard test asserts a real verifier-found gap is NOT promoted.
2. **`StructuredLLMModel` uses `extra="ignore"`, intentionally.** The OpenAI structured-outputs strict `response_format` is the real fail-closed guard — `_enforce_strict_schema` force-sets `additionalProperties:false` on the schema sent to OpenAI **independently of this Pydantic config**. `extra="ignore"` only relaxes the *redundant client-side re-validation*, so a benign extra key when strict mode isn't honoured (non-strict model, truncated response) degrades gracefully instead of nuking the answer. Required-field, type, and Literal/enum enforcement is unchanged — genuinely malformed output still falls back, which is the wanted behaviour. **Do not re-add `extra="forbid"`**: it does not strengthen the real guard and reintroduces the universal-abstention edge.
3. **Free-tier `answer_model` is `gpt-5.4-mini`, not nano.** Quality-over-COGS: nano was the amplifier. Free-tier answer cost rises (documented in `backend/tiers.py`). Reverting to nano without also re-hardening (1)+(2) reopens the regression.

Plus a drift guard: a golden-hash byte-identity test over all 7 active registry prompts (`test_prompt_registry.py`). The PR#5 prompt-registry migration shipped without one; a future `v1.json` edit that drops a required JSON key would otherwise silently universal-abstain via the schema gate. An intentional prompt change must bump the version *and* update the pinned digest.

## Consequences

- **Positive.** Well-grounded answers are no longer abstained for honest hedging. The strict-gate brittleness is removed without weakening the real (server-side) guard. Prompt drift is now a hard test failure, not a silent production regression.
- **Negative / cost.** Free-tier answers cost more (mini ≫ nano per query). Accepted as a correctness/quality decision; revisit only with the COGS math, and never by reverting nano alone.
- **Scope verified.** The dangerous strict-gate class is confined to the /qa answer + verifier path. Retrieval/ingestion LLM call sites (router/planner/landmarks/classifier) all degrade gracefully on drift (heuristic / deterministic plan / no-enrichment / keyword fallback) and were left as-is.
- **Eval-load-bearing.** This changes the verifier logic, the default answer model, and the structured-output posture — all on the surface the FinanceBench / final-eval suites measure. Per ADR-020, baseline-vs-HEAD eval before push; expectation neutral-to-up, unverified until measured.

## Validation

Full backend suite passes (404→406 across the two fix commits) including two new verifier regression tests (the exact bug + the over-correction guard), the schema `extra=ignore` contract tests (extra-key ignored, missing-required still raises), and the 8 prompt-drift golden-hash tests. End-to-end confirmation deferred to the ADR-020 eval.
