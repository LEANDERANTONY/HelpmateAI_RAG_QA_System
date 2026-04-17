# ADR-009: Benchmark-Driven Chunking Default `1200 / 240`

## Status

Accepted

## Context

HelpmateAI originally used:

- `chunk_size = 1200`
- `chunk_overlap = 180`

Those values were reasonable engineering defaults, but they had not gone through the same benchmark scrutiny that we later applied to:

- planner confidence thresholds
- selector weights
- post-retrieval architecture layers

That made chunking one of the important remaining "strong defaults" that still needed evidence.

During the first chunking sweep, we also discovered that index reuse was invalidating the experiment:

- index cache reuse depended on fingerprint and schema version
- it did **not** require matching chunk size or chunk overlap

That meant chunking experiments could silently reuse stale indexes and make different chunk settings look equivalent when they were not.

## Decision

We fixed the index-reuse logic first, then ran a three-stage chunking evaluation:

1. retrieval-level sweep across the labeled retrieval corpus
2. full answer-layer comparison on the positive and negative sets
3. focused external `ragas` cross-check on representative document families

The two main candidate configurations compared in the final decision were:

- `1200 / 180`
- `1200 / 240`

### What the benchmark showed

Retrieval-level result:

- `1200 / 240` improved page-hit and section-hit metrics
- the gain was real but modest

Internal answer-layer result:

- `1200 / 180` retained a slight edge on positive answer support and evidence fragment recall

Focused external `ragas` result:

- `1200 / 240` improved:
  - supported rate
  - answer relevancy
  - context precision
- `1200 / 180` retained a small edge on faithfulness

### Final interpretation

The tradeoff was not between a clearly good and clearly bad chunking configuration.

It was between:

- a more conservative grounding-oriented profile (`1200 / 180`)
- a slightly more answer-directness-oriented profile (`1200 / 240`)

For the current Helpmate product direction, we chose to promote:

- `chunk_size = 1200`
- `chunk_overlap = 240`

Reason:

- the grounding loss was small
- the gains in answer relevancy and context precision were meaningful enough to justify the change

## Consequences

Positive:

- chunking is now benchmark-backed rather than purely intuitive
- the live default reflects the current product preference for slightly better answer quality and context tightness
- the repo now has a clearer evidence trail for why `240` overlap was promoted

Tradeoffs:

- this decision accepts a small faithfulness tradeoff
- future eval-corpus expansion could still reveal document families where `1200 / 180` is safer

## Rejected Alternatives

- keeping `1200 / 180` permanently just because it was the older default
- changing chunking based only on the retrieval sweep
- changing chunking without first fixing stale index reuse

## Notes

This ADR does not claim chunking is fully solved forever.

It records the current best supported default, with the expectation that future work may still explore:

- document-family-specific chunking
- dynamic chunking
- stronger answer-layer and human-reviewed validation

Primary supporting documents:

- `docs/evals/EVAL_ROADMAP.md`
- `docs/evals/reports/chunking_sweep_20260417_140728.json`
- `docs/evals/reports/chunking_answer_ablation_20260417_142748.json`
- `docs/evals/reports/chunking_ragas_compare_20260417_145602.json`
