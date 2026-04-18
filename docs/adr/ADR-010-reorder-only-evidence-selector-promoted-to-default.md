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

However, later failure analysis showed the critical detail:

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
- trigger selector activation only for `global` or `sectional` spread
- disable the ambiguity gate as a production default
- disable the weak-evidence-only gate as a production default
- do **not** prune the candidate list before answer generation
- instead, move the selector-prioritized chunks to the front of the final evidence list and keep the remaining retrieved chunks behind them

Operational default:

- `HELPMATE_EVIDENCE_SELECTOR_ENABLED=true`
- `HELPMATE_EVIDENCE_SELECTOR_PRUNE=false`
- `HELPMATE_EVIDENCE_SELECTOR_TRIGGER_WEAK_EVIDENCE=false`
- `HELPMATE_EVIDENCE_SELECTOR_TRIGGER_SPREAD=true`
- `HELPMATE_EVIDENCE_SELECTOR_TRIGGER_AMBIGUITY=false`

## Evidence

### Initial Isolation Result

Three matched comparisons were used on the same selector-eval corpus and focused `ragas` subset.

Primary reports:

- `docs/evals/reports/evidence_selector_mode_compare_20260418_164811.json`
- `docs/evals/reports/selector_answer_mode_compare_20260418_170916.json`
- `docs/evals/reports/selector_ragas_mode_compare_20260418_184714.json`

Retrieval-level result:

- selector off objective: `0.7674`
- selector prune objective: `0.7255`
- selector reorder-only objective: `0.7757`

Answer-layer result:

- planner+rereanker supported rate: `0.8421`
- selector prune supported rate: `0.8289`
- selector reorder-only supported rate: `0.8553`

Focused external `ragas` result:

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

- the negative selector result from ADR-008 applied to prune mode
- it did not apply to reorder-only selection

### Reorder-Only Calibration Follow-Up

We then ran the remaining selector sweeps in the correct reorder-only mode.

Primary follow-up reports:

- `docs/evals/reports/evidence_selector_weight_sweep_20260418_212632.json`
- `docs/evals/reports/selector_gap_threshold_sweep_20260418_213107.json`
- `docs/evals/reports/selector_threshold_answer_compare_20260418_222716.json`
- `docs/evals/reports/selector_threshold_ragas_compare_20260418_230856.json`
- `docs/evals/reports/selector_trigger_mode_compare_20260418_231133.json`
- `docs/evals/reports/selector_trigger_answer_compare_20260418_233748.json`
- `docs/evals/reports/selector_trigger_ragas_compare_20260419_003001.json`

What was learned:

- reorder-only weight tuning remained a broad plateau
  - `rank_weight = 0.00` through `0.75` all tied on retrieval objective at `0.7757`
  - only very high rank-weighting regressed slightly
- ambiguity-threshold tuning did not justify ambiguity-triggered selection as a production default
  - `0.04 -> 0.10` tied on retrieval objective
  - `0.08` improved positive supported rate in one answer-layer run, but also worsened negative abstention
  - always-on produced the strongest focused `ragas` grounding, but at the cost of triggering on every eligible query
- trigger-source isolation gave the cleaner policy:
  - `weak_only` was effectively inactive on the benchmark
  - `combined` preserved the old broad gate but underperformed the better-targeted modes overall
  - `spread_only` kept strong answer support and answer relevancy while cutting selector usage roughly in half versus always-on
  - `always_on` won on faithfulness and context precision, but paid the full latency cost

Representative calibration outcomes:

- selector trigger rate on the current benchmark:
  - `spread_only`: `42.1%` retrieval eval, `52.9%` focused `ragas`
  - `combined`: `88.2%` retrieval eval, `94.1%` focused `ragas`
  - `always_on`: `96.1%` retrieval eval, `100%` focused `ragas`
- focused `ragas` comparison:
  - `spread_only`: supported `0.9706`, faithfulness `0.9534`, answer relevancy `0.6501`, context precision `0.9404`
  - `combined`: supported `0.9412`, faithfulness `0.9265`, answer relevancy `0.6088`, context precision `0.9346`
  - `always_on`: supported `0.9412`, faithfulness `0.9657`, answer relevancy `0.6415`, context precision `0.9567`

Interpretation:

- ambiguity-triggered reorder-only selection was not a productive default
- spread-only activation preserved the main answer-quality benefit with materially lower activation frequency than always-on
- always-on remains a valid future option if product priorities shift further toward maximum grounding over bounded latency

## Consequences

Positive:

- the selector layer is now benchmark-justified again
- the architecture keeps the selector's evidence disambiguation benefit without throwing away useful support
- the production trigger policy is simpler and lower-latency than the earlier combined gate

Tradeoffs:

- the selector still adds an extra answer-path LLM call
- latency and cost remain higher than planner+rereanker alone
- the layer should remain benchmark-governed; future regressions should be evaluated against reorder-only, not the retired prune mode

## Rejected Alternatives

- keeping the old prune-based selector disabled permanently without isolating the pruning effect
- promoting the prune-based selector despite the clear support/coverage regressions
- assuming selector value could be inferred from weight tuning alone
- keeping the ambiguity-triggered combined gate after the trigger-isolation sweep
- keeping weak-evidence-only activation logic when it did not activate meaningfully on the benchmark

## Notes

This ADR supersedes the selector-specific conclusion in ADR-008.

ADR-008 remains valid for:

- reranker as a default layer
- planner/router as a modest default layer
- benchmark-driven governance of architecture choices

But ADR-010 replaces ADR-008's selector decision with:

- selector stays
- reorder-only mode is the justified default
- spread-only triggering is the justified production policy
- prune mode is retained only as an experimental comparison path if needed
