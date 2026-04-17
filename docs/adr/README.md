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
  - evidence selector remains experimental rather than default
- the newest retrieval-tuning governance change is:
  - chunking default promoted from `1200 / 180` to `1200 / 240` after retrieval, answer-layer, and focused `ragas` validation
