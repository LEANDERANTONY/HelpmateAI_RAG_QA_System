# Helpmate Architecture Scorecard

This document is the current working summary of what the evaluation stack says about the major Helpmate architecture layers.

It is intentionally short and decision-oriented.

## Current Recommendation

- keep the reranker
- keep the calibrated planner/router layer
- keep the evidence selector in reorder-only mode

## Current Layer Summary

| Layer | Current status | Quality impact | Cost / complexity impact | Current decision |
| --- | --- | --- | --- | --- |
| Reranker | Measured | Strong positive | Adds memory and compute overhead | Keep |
| Planner / router fallback | Measured | Small positive | Adds low-frequency LLM routing calls and logic complexity | Keep, but treat as modest |
| Evidence selector | Re-tested in reorder-only mode | Positive on answer support and context precision without pruning loss | Adds an extra LLM call and more logic | Keep in reorder-only mode |

## Latency / Cost Snapshot

Answer-path latency benchmark report:

- `docs/evals/reports/latency_cost_benchmark_20260413_210032.json`

Overall runtime and operational proxy summary:

| Variant | Mean total latency | P95 latency | Estimated LLM stage calls / question |
| --- | --- | --- | --- |
| Baseline | `2670 ms` | `4418 ms` | `0.991` |
| Reranker only | `2917 ms` | `4347 ms` | `0.991` |
| Planner + reranker | `3629 ms` | `9850 ms` | `1.117` |
| Full stack | `4539 ms` | `7440 ms` | `1.964` |

Operational interpretation:

- reranker adds moderate retrieval-time overhead for a large quality gain
- planner adds noticeable latency because the LLM fallback activates on about `12.6%` of questions
- selector is still the most expensive incremental layer in the active stack, nearly doubling estimated LLM-stage calls per question versus reranker-only
- the reorder-only design now justifies that extra cost on the current benchmark, but it should remain benchmark-governed because latency is still real

## External `ragas` Check

Focused `ragas` stack comparison report:

- `docs/evals/reports/ragas_stack_ablation_20260413_222404.json`

Scope:

- document families:
  - health policy
  - thesis
  - `pancreas7`
- variants:
  - `reranker_only`
  - `planner_reranker`
  - `full_stack`

Original top-line `ragas` results for the prune-based selector run:

| Variant | Supported rate | Faithfulness | Answer relevancy | Context precision |
| --- | --- | --- | --- | --- |
| Reranker only | `0.9118` | `0.9044` | `0.6097` | `0.8962` |
| Planner + reranker | `0.9412` | `0.9412` | `0.5679` | `0.8840` |
| Full stack | `0.9412` | `0.8672` | `0.6533` | `0.9853` |

This comparison is now historical.

The prune-based selector conclusion was overturned by the later reorder-only ablation recorded in:

- `docs/evals/reports/selector_ragas_mode_compare_20260418_184714.json`

Reorder-only result vs planner+rereanker:

- planner+rereanker:
  - faithfulness `0.9310`
  - answer relevancy `0.6555`
  - context precision `0.9036`
- selector reorder-only:
  - faithfulness `0.9657`
  - answer relevancy `0.6436`
  - context precision `0.9608`

Interpretation:

- the original negative selector finding was caused by evidence pruning, not by evidence reordering itself
- once the selector was allowed to reorder without dropping support, it became a net positive layer again

## Evidence Selector

### Weight Tuning

The selector blend weights were tuned on the labeled retrieval datasets.

Current tuned default:

- `rank_weight = 0.25`
- `llm_weight = 0.75`

Reason:

- it sits inside the best-performing plateau from the selector-weight sweep
- it is more defensible than the earlier hand-set blend

Important caveat:

- tuning the selector weights did not by itself prove the selector was valuable
- the earlier prune-based selector still failed the first benchmark pass
- the later reorder-only ablation is what actually justified re-enabling the layer

### Prune vs Reorder-Only Result

Retrieval-level comparison:

- selector off objective: `0.7674`
- selector prune objective: `0.7255`
- selector reorder-only objective: `0.7757`

Answer-layer comparison:

- planner+rereanker supported rate: `0.8421`
- selector prune supported rate: `0.8289`
- selector reorder-only supported rate: `0.8553`

Evidence coverage:

- citation page-hit:
  - planner+rereanker: `0.8421`
  - selector prune: `0.7632`
  - selector reorder-only: `0.8421`
- evidence fragment recall:
  - planner+rereanker: `0.6971`
  - selector prune: `0.6434`
  - selector reorder-only: `0.6971`

Focused `ragas` comparison:

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

Selector gate usage on the benchmark:

- positive eval set: `68 / 76` = `89.5%`
- focused `ragas` subset: `32 / 34` = `94.1%`

Current interpretation:

- the original selector failure came from pruning away support
- reorder-only selection preserves evidence coverage while improving ordering
- the selector is now justified again, but only in reorder-only mode

## Reranker

Reranker ablation result:

- reranker off objective: `0.6164`
- reranker on objective: `0.7674`
- page hit rate: `0.6579 -> 0.8421`
- MRR: `0.4890 -> 0.6820`
- fragment recall: `0.6357 -> 0.7202`

Answer-layer result:

- supported rate: `0.8026 -> 0.8816`
- citation page-hit rate: `0.6974 -> 0.8684`
- evidence fragment recall mean: `0.6461 -> 0.7331`

Current interpretation:

- the reranker is clearly justified

## Planner / Router

### Threshold Calibration

The planner/router gate was treated as a tunable hyperparameter, not a fixed assumption.

Current calibrated defaults:

- `HELPMATE_PLANNER_CONFIDENCE_THRESHOLD = 0.70`
- `HELPMATE_ROUTER_CONFIDENCE_THRESHOLD = 0.62`

These replaced the earlier:

- `0.74`
- `0.60`

### Planner Ablation

Calibrated planner/router vs deterministic-only routing:

- page hit rate: unchanged at `0.8553`
- MRR: unchanged at `0.7018`
- fragment recall: `0.7191 -> 0.7224`

Answer-layer comparison:

- supported rate: unchanged at `0.8816`
- citation page-hit rate: unchanged at `0.8684`
- evidence fragment recall mean: `0.7331 -> 0.7364`

Current interpretation:

- the planner/router layer is a small positive
- it is not a dramatic win, but it is also not dead weight

## Answer-Layer Variant Comparison

Current answer-layer benchmark summary:

| Variant | Supported rate | Citation page-hit rate | Evidence fragment recall | Negative abstention |
| --- | --- | --- | --- | --- |
| Baseline | `0.8026` | `0.6974` | `0.6461` | `0.9714` |
| Reranker only | `0.8816` | `0.8684` | `0.7331` | `0.9714` |
| Planner + reranker | `0.8816` | `0.8684` | `0.7364` | `0.9714` |
| Full stack | `0.8553` | `0.7763` | `0.6526` | `0.9714` |

Interpretation:

- reranker is the main answer-quality gain
- planner gives a small incremental lift
- selector reduces answer-layer quality on the current benchmark
- negative abstention is unchanged across the tested variants

## Current Architecture Position

If we had to choose today based only on benchmark evidence:

- keep:
  - reranker
  - calibrated planner/router fallback
  - reorder-only evidence selector
- keep under review:
  - whether selector trigger conditions can be narrowed to reduce latency without losing the current gain

## What Still Remains

- expand the eval corpus later with more documents to improve robustness
