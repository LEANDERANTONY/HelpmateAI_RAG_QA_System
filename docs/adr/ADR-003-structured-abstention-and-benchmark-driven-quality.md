# ADR-003: Structured Abstention and Benchmark-Driven Quality Control

- Status: Accepted
- Date: 2026-04-01

## Context

Early answer generation could still produce vague unsupported responses even when retrieval was weak. At the same time, retrieval benchmarking needed stronger discipline so quality claims were grounded in saved reports rather than impressions.

## Decision

Introduce:

- a structured answer contract with explicit `supported` status
- stricter abstention behavior for unsupported questions
- positive and negative eval datasets
- saved benchmark reports under `docs/evals/reports/`
- OpenAI hosted retrieval as a comparison baseline

## Consequences

Positive:

- unsupported questions are now handled explicitly rather than indirectly
- evaluation became a repeatable workflow
- local RAG versus hosted retrieval can be compared on the same document-specific benchmark
- benchmark reports now form part of the repo history

Tradeoffs:

- benchmark design quality matters a lot; poor labels can create false negatives
- answer prompts and output parsing became more structured and slightly more complex

## Challenges Observed

- several early retrieval misses were actually benchmark-label problems, not pure retrieval failures
- negative evals initially judged abstention through wording heuristics rather than a typed field
- OpenAI hosted benchmark quality varied by document and index readiness, so comparisons must be interpreted carefully
