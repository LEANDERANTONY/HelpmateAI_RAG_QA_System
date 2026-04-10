# ADR-007: Document-Topology Planning And Bounded Evidence Selection

## Status

Accepted

## Context

HelpmateAI had already moved beyond a basic hybrid RAG stack, but two gaps remained:

- structure was present mostly as metadata and ranking hints rather than as active retrieval control
- some answer failures were not true retrieval failures; the correct chunk was already in top `k` but not at rank 1

At the same time, the system needed to preserve:

- chunk-first exact grounding for factual questions
- multi-page evidence for distributed concepts
- unsupported-question guardrails
- a deterministic-first architecture rather than a fully LLM-driven planner

## Decision

We adopted two linked changes.

### 1. Document-topology retrieval planning

Add a deterministic `RetrievalPlan` before retrieval that predicts:

- `intent_type`
- `evidence_spread`
- `constraint_mode`
- `preferred_route`
- target region kinds and region ids

Retrieval now uses:

- `chunk_first` for exact local questions
- `synopsis_first` for broader section/global questions
- `hybrid_both` for mixed and detail-sensitive cases
- soft multi-region structural guidance with global fallback
- hard constraints only for explicit page, clause, or section references

Section synopses and topology edges are stored locally and used as an active retrieval surface.

### 2. Bounded evidence selection after reranking

After retrieval and reranking, add an optional evidence selector that:

- only sees the top retrieved candidates
- keeps ranking order as a prior
- can promote lower-ranked but more direct evidence
- never invents evidence
- never bypasses unsupported retrieval guardrails

This layer exists to fix rank-order mistakes without changing the retrieval problem itself.

## Consequences

Positive:

- structure is now an active control signal rather than passive metadata
- broad and distributed questions have a clearer retrieval path
- planner behavior is measurable with structure-aware metrics
- final evidence choice can improve even when retrieval already found the right chunk in top `k`

Tradeoffs:

- planner errors can bias retrieval more directly than before
- synopsis quality matters more than it did previously
- latency increases slightly when the evidence selector runs
- broad paper-summary questions are still the hardest remaining case

## Rejected Alternatives

- reintroducing model-based query rewriting
- making the planner fully LLM-driven for every query
- hard-locking retrieval to one section for broad questions
- adopting a neural database or compressed latent store as the next quality step

## Notes

This ADR does not replace the earlier guardrail ADR.

Instead, it builds on it:

- unsupported retrieval still short-circuits before answer generation
- the evidence selector only runs on plausible retrieved evidence
- final answers remain grounded on raw chunks, not synopsis text alone
