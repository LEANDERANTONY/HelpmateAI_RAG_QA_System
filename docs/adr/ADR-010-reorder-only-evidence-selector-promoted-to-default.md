# ADR-010: Reorder-Only Evidence Selector Promoted To Default

## Status

Accepted

## Context

ADR-008 correctly removed the original bounded evidence selector from the default stack because the measured implementation at that time pruned the final evidence set down to a very small shortlist.

That version of the selector did two things at once:

- re-ranked ambiguous evidence candidates with an LLM-guided blend
- dropped non-selected candidates before answer generation

The benchmark result was negative:

- retrieval objective dropped
- answer-layer supported rate dropped
- focused external `ragas` showed a tradeoff rather than a clear win

However, the later failure analysis showed the critical detail:

- the selector was not only changing order
- it was shrinking the answer context window from the reranked retrieval set to a bounded `max_evidence` subset

That meant the selector experiment had conflated:

- better evidence ordering
- loss of supporting context

We therefore needed a second ablation that isolated those two effects.

## Decision

Promote the evidence selector back into the default stack, but only in reorder-only mode.

This means:

- keep the selector LLM scoring and prioritization logic
- keep the tuned blend:
  - `rank_weight = 0.25`
  - `llm_weight = 0.75`
- keep the same selector trigger conditions:
  - weak evidence
  - `global` or `sectional` spread
  - narrow top-gap ambiguity
- do **not** prune the candidate list before answer generation
- instead, move the selector-prioritized chunks to the front of the final evidence list and keep the remaining retrieved chunks behind them

Operational default:

- `HELPMATE_EVIDENCE_SELECTOR_ENABLED=true`
- `HELPMATE_EVIDENCE_SELECTOR_PRUNE=false`

## Evidence

Three matched comparisons were used on the same selector-eval corpus and focused `ragas` subset.

Primary reports:

- `docs/evals/reports/evidence_selector_mode_compare_20260418_164811.json`
- `docs/evals/reports/selector_answer_mode_compare_20260418_170916.json`
- `docs/evals/reports/selector_ragas_mode_compare_20260418_184714.json`

### Retrieval-Level Result

Compared variants:

- selector off
- selector prune
- selector reorder-only

Top-line result:

- selector off objective: `0.7674`
- selector prune objective: `0.7255`
- selector reorder-only objective: `0.7757`

Interpretation:

- the negative retrieval result from the earlier selector experiment came from pruning
- reorder-only not only recovered the loss, it slightly exceeded selector-off on retrieval objective

### Answer-Layer Result

Top-line positive answer result:

- planner+rereanker supported rate: `0.8421`
- selector prune supported rate: `0.8289`
- selector reorder-only supported rate: `0.8553`

Evidence quality:

- citation page-hit:
  - planner+rereanker: `0.8421`
  - selector prune: `0.7632`
  - selector reorder-only: `0.8421`
- evidence fragment recall:
  - planner+rereanker: `0.6971`
  - selector prune: `0.6434`
  - selector reorder-only: `0.6971`

Interpretation:

- pruning damaged support and coverage
- reorder-only preserved the evidence coverage profile while slightly improving supported answers

### Focused External `ragas` Result

Compared variants on:

- health policy
- thesis
- `pancreas7`

Top-line result:

- planner+rereanker:
  - faithfulness `0.9310`
  - answer relevancy `0.6555`
  - context precision `0.9036`
- selector prune:
  - faithfulness `0.9174`
  - answer relevancy `0.6015`
  - context precision `0.9158`
- selector reorder-only:
  - faithfulness `0.9657`
  - answer relevancy `0.6436`
  - context precision `0.9608`

Interpretation:

- reorder-only produced the strongest faithfulness score
- reorder-only also produced the strongest context precision score
- answer relevancy stayed close to planner+rereanker and clearly above prune mode

This overturned the earlier selector conclusion from ADR-008 for the original reason:

- the negative conclusion applied to the prune-based selector
- it does not apply to reorder-only selection

## Consequences

Positive:

- the selector layer is now benchmark-justified again
- the architecture keeps the selector’s evidence disambiguation benefit without throwing away useful support
- the default answer path gains stronger context precision with better grounding than the earlier prune-based design

Tradeoffs:

- the selector still adds an extra answer-path LLM call
- latency and cost remain higher than planner+rereanker alone
- the layer should remain benchmark-governed; future regressions should be evaluated against reorder-only, not the retired prune mode

## Rejected Alternatives

- keeping the old prune-based selector disabled permanently without isolating the pruning effect
- promoting the prune-based selector despite the clear support/coverage regressions
- assuming selector value could be inferred from weight tuning alone

## Notes

This ADR supersedes the selector-specific conclusion in ADR-008.

ADR-008 remains valid for:

- reranker as a default layer
- planner/router as a modest default layer
- benchmark-driven governance of architecture choices

But ADR-010 replaces ADR-008's selector decision with:

- selector stays
- reorder-only mode is the justified default
- prune mode is retained only as an experimental comparison path if needed
