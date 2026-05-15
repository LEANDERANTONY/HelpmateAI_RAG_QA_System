# Architecture Decision Records

This directory tracks the major architecture decisions behind HelpmateAI.

Current ADRs:

**Core RAG architecture (ADR-001..014):**

- `ADR-001-streamlit-first-backend-ready-rag-app.md`
- `ADR-002-local-first-hybrid-rag-with-chroma-and-evals.md`
- `ADR-003-structured-abstention-and-benchmark-driven-quality.md`
- `ADR-004-document-intelligence-layer-for-structure-aware-retrieval.md`
- `ADR-005-dual-retrieval-routing-with-lightweight-llm-tiebreaker.md`
- `ADR-006-deterministic-weak-evidence-recovery-and-guardrails.md`
- `ADR-007-document-topology-planning-and-bounded-evidence-selection.md`
- `ADR-008-benchmark-driven-stack-defaults-and-experimental-selector.md`
- `ADR-009-benchmark-driven-chunking-default-1200-240.md`
- `ADR-010-reorder-only-evidence-selector-promoted-to-default.md`
- `ADR-011-partial-grounded-answers-and-support-guardrail-eval.md`
- `ADR-012-smart-section-profiles-and-orchestrated-scope.md`
- `ADR-013-ephemeral-workflow-run-traces.md`
- `ADR-014-in-app-source-viewer-with-pdfjs-and-docx-rendition.md`

**Tier enforcement + payments (ADR-015..017):**

- `ADR-015-tier-resolution-via-single-shim-function.md`
- `ADR-016-atomic-quota-increment-and-anon-execute-revoke.md`
- `ADR-017-lemon-squeezy-as-merchant-of-record-for-v1.md`

**Observability + compliance + cost discipline (ADR-018..020):**

- `ADR-018-observability-stack-sentry-and-posthog.md`
- `ADR-019-eu-cookie-consent-banner-and-gdpr-analytics-gating.md`
- `ADR-020-manual-only-nightly-eval-at-pre-revenue-stage.md`

Usage notes:

- ADRs describe why a decision was made, not just what the code looks like today
- later ADRs may refine or partially supersede earlier ones
- if the product direction changes materially, add a new ADR instead of rewriting history

Current state note:

- the **core RAG architecture** described by ADR-001..014 is still valid
- the **tier enforcement + payments stack** (ADR-015..017) is live in production with `resolve_user_tier` reading from the `subscriptions` table; every user resolves to `"free"` until the LS variant IDs flip live
- the **observability + compliance + cost-discipline group** (ADR-018..020) is the most recent series:
  - dual-vendor stack (Sentry + PostHog) on free tier with paired Sentry projects per product
  - EU cookie consent banner gating non-essential analytics while keeping crash reporting always-on under legitimate interest
  - `nightly_eval` switched to manual-only mode at pre-revenue stage; re-enable is a three-step flip when revenue justifies the ~\$15-26/mo Mon+Thu cadence (or ~\$45-90/mo daily)
- the newest retrieval changes are:
  - deterministic document-topology planning
  - low-confidence indexing-time structure repair
  - synopsis-first retrieval with soft structural guidance
  - a dedicated `global_summary_first` route for broad paper-summary questions
  - bounded post-rerank evidence selection
- the newest architecture governance change is:
  - benchmark-driven confirmation that reranker stays
  - planner/router stays as a modest positive
  - prune-based evidence selection was rejected, but reorder-only evidence selection is now benchmark-validated and promoted to the default stack
- the newest retrieval-tuning governance change is:
  - chunking default promoted from `1200 / 180` to `1200 / 240` after retrieval, answer-layer, and focused `ragas` validation
- the newest calibration closure is:
  - selector trigger policy now defaults to spread-only activation
  - structure-repair threshold remains `0.62`
  - topology edge sets remained benchmark-invariant on the current corpus
- the newest support-guardrail closure is:
  - weak/unsupported retrieval thresholds remain unchanged after sweep testing
  - generation now permits grounded partial answers with missing coverage explained in the visible answer and `reason`
  - support-status verification can recover a first-pass refusal to full support only when all required facts are directly grounded and no gap/inferential language remains
  - `support_guardrail_eval` tracks calibration negatives and held-out manual questions together
- the newest experimental retrieval architecture change is:
  - indexing now records generic section profile metadata for chapter, role, page range, and scope labels
  - the older hybrid-indexing candidate was integrated selectively by keeping policy-aware semantic indexing and rejecting stale rollbacks
  - a retrieval orchestrator can resolve explicit local scope to validated section IDs
  - hard orchestrated scope disables global fallback and filters final evidence to the allowed sections
  - lean RAGAS regression against `main` improved answer relevancy and context precision on the targeted upgrade suite without reducing supported rate
  - lean vendor comparison showed stronger supported rate, answer relevancy, and context precision than OpenAI File Search or Vectara on the same targeted suite
- the newest workflow observability change is:
  - each uncached QA run now writes an ephemeral run trace
  - traces store decision metadata, candidate IDs/scores/previews, and support/citation outcomes
  - traces follow the same workspace retention window locally and in Supabase
- the newest product-surface change is:
  - an in-app source viewer (Read Mode) replaces the dead "Open in source" link with a layout posture that puts the chat and the PDF side-by-side on desktop and as a draggable bottom sheet on mobile
  - DOCX uploads now produce a viewable PDF rendition at ingest via LibreOffice, so the viewer experience is uniform across formats
  - a new `GET /documents/{id}/file` endpoint serves the rendition inline (with HTTP Range support for PDF.js streaming) and the original source under `?download=1`
  - the viewer uses a hint-page + ±3 page window strategy to locate the cited passage, falling back to a soft banner rather than a far-page jump when the chunk's anchor text cannot be matched
