# Architecture Decision Records

This directory tracks the major architecture decisions behind HelpmateAI.

Current ADRs:

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

Usage notes:

- ADRs describe why a decision was made, not just what the code looks like today
- later ADRs may refine or partially supersede earlier ones
- if the product direction changes materially, add a new ADR instead of rewriting history

Current state note:

- the core RAG architecture described by these ADRs is still valid
- the newest retrieval changes are now:
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
  - generation now permits grounded partial answers with missing coverage explained in `reason`
  - `support_guardrail_eval` tracks calibration negatives and held-out manual questions together
