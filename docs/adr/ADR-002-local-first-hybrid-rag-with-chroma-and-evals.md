# ADR-002: Local-First Hybrid RAG With Chroma and Evaluation Harnesses

- Status: Accepted
- Date: 2026-04-01

## Context

The original notebook was too opaque and brittle for reliable document QA. We needed persistent indexes, inspectable retrieval behavior, and a way to compare our pipeline against an external baseline rather than relying on intuition alone.

## Decision

Use a local-first RAG stack built from:

- Chroma as the persisted vector store
- dense retrieval plus TF-IDF lexical retrieval
- reciprocal-rank style fusion
- optional reranking
- persisted index reuse
- conservative answer caching
- offline retrieval benchmarks and an OpenAI file-search comparison harness

## Consequences

Positive:

- better inspectability than a fully hosted retrieval abstraction
- repeatable local development and benchmarking
- easier debugging of chunking, retrieval, and ranking behavior
- measurable product differentiation beyond generic file chat

Tradeoffs:

- local retrieval quality depends heavily on chunking and metadata design
- Chroma introduced noisy telemetry warnings and metadata constraints that had to be handled explicitly
- benchmark quality became part of the product work, not an optional extra

## Challenges Observed

- early retrieval quality was weak until the benchmark labels were corrected and retrieval notes were surfaced
- Chroma telemetry warnings created noisy terminal output
- Chroma only accepts scalar metadata values, which later required sanitization at the storage boundary

## Follow-up

This decision remains active, but future ADRs may refine chunking and retrieval routing further.
