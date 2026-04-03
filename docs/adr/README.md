# Architecture Decision Records

This directory tracks the major architecture decisions behind HelpmateAI.

Current ADRs:

- `ADR-001-streamlit-first-backend-ready-rag-app.md`
- `ADR-002-local-first-hybrid-rag-with-chroma-and-evals.md`
- `ADR-003-structured-abstention-and-benchmark-driven-quality.md`
- `ADR-004-document-intelligence-layer-for-structure-aware-retrieval.md`
- `ADR-005-dual-retrieval-routing-with-lightweight-llm-tiebreaker.md`

Usage notes:

- ADRs describe why a decision was made, not just what the code looks like today
- later ADRs may refine or partially supersede earlier ones
- if the product direction changes materially, add a new ADR instead of rewriting history

Current state note:

- the core RAG architecture described by these ADRs is still valid
- the next likely ADR should be about frontend extraction or a custom web frontend phase, not another large retrieval-core rewrite
