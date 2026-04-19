# Helpmate Architecture Scorecard

This document is the short, decision-oriented snapshot of what the evaluation stack currently says about the Helpmate architecture.

## Current Recommendation

- keep the reranker
- keep the calibrated planner/router layer
- keep the evidence selector in reorder-only mode
- use spread-only selector triggering as the production policy
- keep `structure_repair_confidence_threshold = 0.62`
- keep `synopsis_section_window = 4`
- keep the current synopsis top-k pool (`dense=8`, `lexical=8`, `fused=5`)
- keep the current topology edge sets for `soft_local` and `soft_multi_region`
- change `global_fallback_top_k` from `4` to `3`
- change `planner_candidate_region_limit` from `6` to `10`

## Current Layer Summary

| Layer | Current status | Quality impact | Cost / complexity impact | Current decision |
| --- | --- | --- | --- | --- |
| Reranker | Measured | Strong positive | Adds memory and compute overhead | Keep |
| Planner / router fallback | Measured | Small positive | Adds low-frequency LLM routing calls and logic complexity | Keep, but treat as modest |
| Evidence selector | Re-tested and recalibrated | Positive in reorder-only mode | Adds an extra LLM call and more logic | Keep, spread-only trigger |
| Structure repair threshold | Swept | No better threshold justified | Index-time LLM call on a minority of documents | Keep `0.62` |
| Synopsis retrieval defaults | Swept | Stable plateau | Low runtime risk, medium logic importance | Keep current window and top-k pool |
| Topology edge types | Ablated | Invariant on current benchmark | No extra runtime difference, but extra logic surface | Keep current edge sets |
| Global fallback pool | Swept | Smaller pool is slightly better | Affects broad-summary recall and noise | Reduce to `3` |
| Planner region limit | Swept | Broader candidate set helps overall routing quality | Minimal runtime/config complexity | Raise to `10` |

## Selector Scorecard

### Reorder-Only Result

Matched comparison:

- selector off objective: `0.7674`
- selector prune objective: `0.7255`
- selector reorder-only objective: `0.7757`

Answer-layer:

- planner+rereanker supported rate: `0.8421`
- selector prune supported rate: `0.8289`
- selector reorder-only supported rate: `0.8553`

Focused `ragas`:

- planner+rereanker:
  - faithfulness `0.9310`
  - answer relevancy `0.6555`
  - context precision `0.9036`
- selector reorder-only:
  - faithfulness `0.9657`
  - answer relevancy `0.6436`
  - context precision `0.9608`

Interpretation:

- the original selector failure came from pruning away support
- reorder-only preserved evidence coverage while improving ordering

### Trigger Calibration Result

Weight sweep in reorder-only mode:

- `rank_weight = 0.00` through `0.75` tied on retrieval objective at `0.7757`
- current `0.25 / 0.75` remains a valid default inside that plateau

Gap-threshold sweep:

- `0.04 -> 0.10` tied on retrieval objective
- ambiguity-triggered selection did not justify itself cleanly once answer-layer and focused `ragas` tradeoffs were included

Trigger-source isolation:

| Mode | Retrieval objective | Positive supported rate | Negative abstention | Focused `ragas` faithfulness | Focused `ragas` answer relevancy | Focused `ragas` context precision | Trigger rate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `weak_only` | `0.7674` | `0.8684` | `0.9714` | `0.9093` | `0.6274` | `0.9158` | `0.0%` |
| `spread_only` | `0.7727` | `0.8816` | `0.9714` | `0.9534` | `0.6501` | `0.9404` | `42.1%` retrieval / `52.9%` `ragas` |
| `combined` | `0.7757` | `0.8684` | `1.0000` | `0.9265` | `0.6088` | `0.9346` | `88.2%` retrieval / `94.1%` `ragas` |
| `always_on` | `0.7753` | `0.8816` | `0.9714` | `0.9657` | `0.6415` | `0.9567` | `96.1%` retrieval / `100%` `ragas` |

Interpretation:

- weak-evidence-only activation is not useful on the current benchmark
- the old combined gate is no longer the best default
- always-on is a strong grounding mode, but spread-only is the better production tradeoff right now
- spread-only preserves the main answer-quality gain while avoiding near-universal selector activation

## Retrieval-Default Scorecard

### Synopsis Section Window

Report:

- `docs/evals/reports/synopsis_section_window_sweep_20260419_004324.json`

Result:

- `window_4`, `window_5`, and `window_6` tied on the paper-family objective at `0.7923`
- `window_4` also sat on the best overall objective plateau

Decision:

- keep `synopsis_section_window = 4`
- it remains the best current default and is now benchmark-backed rather than intuition-backed

### Synopsis Top-K Pool

Report:

- `docs/evals/reports/synopsis_topk_grid_20260419_005543.json`

Result:

- the tested grid was flat across:
  - `fused=3, dense=6, lexical=6`
  - `fused=4, dense=6, lexical=6`
  - `fused=5, dense=8, lexical=8`
  - `fused=6, dense=8, lexical=8`
  - `fused=8, dense=10, lexical=10`

Decision:

- keep the current pool:
  - `synopsis_dense_top_k = 8`
  - `synopsis_lexical_top_k = 8`
  - `synopsis_fused_top_k = 5`
- the current defaults sit inside a measured plateau

### Global Fallback Pool

Report:

- `docs/evals/reports/global_fallback_topk_sweep_20260419_011043.json`

Result:

- `global_fallback_top_k = 3` produced the best overall objective (`0.7041`)
- the current `4` was close but slightly worse (`0.7037`)
- larger fallback pools steadily degraded the benchmark

Decision:

- reduce `global_fallback_top_k` to `3`

### Planner Candidate Region Limit

Report:

- `docs/evals/reports/planner_candidate_region_limit_sweep_20260419_012403.json`

Result:

- `planner_candidate_region_limit = 10` produced the best overall objective (`0.7530`) and best plan accuracy (`0.7147`)
- the previous default `6` was notably worse overall (`0.7165`)

Decision:

- raise `planner_candidate_region_limit` to `10`

### Structure Repair Threshold

Reports:

- `docs/evals/reports/structure_repair_threshold_sweep_20260419_021637.json`
- `docs/evals/reports/structure_repair_signal_ablation_20260419_020556.json`

Result:

- thresholds `0.50`, `0.55`, and `0.62` all produced the same repair profile:
  - `reportgeneration` repaired
  - `reportgeneration2` not repaired
  - no healthy-document false positives
- `0.68` and `0.75` began triggering false-positive repair on the health-policy benchmark
- deterministic signal ablation showed:
  - `long_document_too_few_sections` is load-bearing for `reportgeneration`
  - `noisy_titles` is also load-bearing for `reportgeneration`
  - `reportgeneration2` is not recoverable through threshold tuning alone under the current heuristic

Decision:

- keep `structure_repair_confidence_threshold = 0.62`
- treat `reportgeneration2` as a heuristic-gap case rather than a threshold-calibration miss
- keep the current penalty set; no simplification is justified yet

### Topology Edge Types

Report:

- `docs/evals/reports/topology_edge_ablation_20260419_022708.json`

Result:

- removing any single edge type produced the same aggregate benchmark objective on the current eval corpus
- tested variants:
  - no `previous_next`
  - no `parent_child`
  - no `same_region_family`
  - no `semantic_neighbor`
- the benchmark was effectively invariant across those variants on:
  - overall objective
  - policy/thesis family objective
  - paper-family objective

Decision:

- keep the current edge sets unchanged
- record topology edges as benchmark-invariant on the current corpus, not as a newly optimized default

## Current Architecture Position

If we had to choose today based on the benchmark stack:

- keep:
  - reranker
  - calibrated planner/router fallback
  - reorder-only evidence selector
- set selector policy to:
  - spread-only trigger
  - no ambiguity trigger
  - no weak-evidence-only trigger
- keep:
  - `synopsis_section_window = 4`
  - `synopsis_dense_top_k = 8`
  - `synopsis_lexical_top_k = 8`
  - `synopsis_fused_top_k = 5`
  - `structure_repair_confidence_threshold = 0.62`
  - current topology edge sets
- change:
  - `global_fallback_top_k = 3`
  - `planner_candidate_region_limit = 10`

## What Still Remains

- add answer-quality eval coverage for the newer report-generation datasets
- add optional gold-answer fields for a selected benchmark subset
- rerun the external vendor comparison only after a materially new retrieval or answer-layer change
