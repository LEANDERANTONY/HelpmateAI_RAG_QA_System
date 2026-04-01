# ADR-004: Document-Intelligence Layer for Structure-Aware Retrieval

- Status: Accepted
- Date: 2026-04-02

## Context

Policy-document benchmarks showed strong performance, but the team correctly identified a risk: repeated document-specific tuning would overfit to one class of document. We needed a more general retrieval improvement than ad hoc boosts.

## Decision

Add a lightweight document-intelligence layer ahead of final retrieval and generation:

- infer section headings and clause identifiers during ingestion
- tag pages and chunks with semantic metadata such as `section_path`, `clause_ids`, and `content_type`
- perform semantic block-aware chunking instead of only flat page-window chunking
- classify questions into broad query types such as `definition_lookup`, `waiting_period_lookup`, and `process_lookup`
- use those inferred signals as soft ranking preferences during retrieval

## Consequences

Positive:

- retrieval behavior is less tied to one document family
- debugging is improved because the system can explain preferred content types and query classification
- the architecture is now positioned for future hierarchical or section-first retrieval

Tradeoffs:

- the middle layer is another moving part to validate
- current classification is still heuristic rather than fully learned
- broad query classes can misfire on academic or narrative documents

## Challenges Observed

- Chroma rejected list-valued metadata, so structured metadata had to be sanitized before index writes
- early query-class heuristics were better for policy documents than for thesis-style narrative questions
- section-aware retrieval improved portability, but it did not eliminate clause-level misses or narrative synthesis misses

## Follow-up

Likely next steps:

- clause-first or section-first retrieval
- section-summary embeddings
- better query typing for academic and research documents
