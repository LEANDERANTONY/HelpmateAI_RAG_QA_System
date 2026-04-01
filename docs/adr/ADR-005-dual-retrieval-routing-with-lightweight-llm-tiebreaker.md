# ADR-005: Dual Retrieval Routing with Lightweight LLM Tie-Breaker

- Status: Accepted
- Date: 2026-04-02

## Context

The structure-aware retrieval layer improved portability, but benchmark work on theses and research papers showed a persistent gap: chunk-first retrieval remained strong for factual questions, while broader synthesis questions needed better section-level navigation.

The team wanted to improve broad-question handling without weakening the existing policy-document benchmark.

## Decision

Add a dual retrieval design:

- keep `chunk_first` retrieval as the main exact-grounding path
- add `section_first` retrieval to identify relevant document regions before chunk retrieval
- add `hybrid_both` for mixed questions
- introduce a lightweight query router to choose the retrieval path
- allow a lightweight LLM-assisted tie-breaker only when heuristic routing is low-confidence

This routing layer remains intentionally bounded. It does not answer user questions, plan tasks, or behave as a full agent.

## Consequences

Positive:

- broad paper and thesis questions can use section context without replacing the strong chunk path
- routing decisions are now explicit and inspectable in retrieval notes
- the architecture can evolve toward better hierarchical retrieval without another major restructure

Tradeoffs:

- the system now has more moving parts to benchmark and validate
- the lightweight LLM router adds some latency on low-confidence cases
- the LLM router is not yet a guaranteed accuracy gain across every benchmark

## Challenges Observed

- rewritten broad questions can lose summary cues and drift back toward factual routing
- section quality matters more than routing sophistication on academic documents
- the most stubborn misses still come from imperfect section construction, front matter, appendices, or bibliography-heavy pages

## Follow-up

Likely next steps:

- improve academic-paper and thesis section parsing
- suppress references, front matter, and appendix noise more aggressively
- deepen section-aware reranking for narrative and synthesis-heavy questions
