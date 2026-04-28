# Benchmark Summary

This file is the current high-level benchmark snapshot for HelpmateAI.

## Policy

- use `ragas` as the only answer-quality evaluation meter going forward
- keep `Vectara` as the main external retrieval baseline
- keep `OpenAI File Search` as a historical/reference retrieval baseline only
- do not use Vectara factual-consistency as a decision-making metric in routine benchmarking
- describe vendor comparisons as tested-configuration comparisons, not universal product superiority claims

Reason:

- `ragas` is more interpretable for the kinds of answer-quality tradeoffs we care about
- Vectara factual-consistency produced unstable or formatting-sensitive readings for our answer style
- Vectara retrieval is the strongest external retrieval baseline we have tested so far

## Methodology Boundary

The current benchmark answers this question:

> On this project workload, does HelpmateAI's retrieval pipeline produce better answer-quality signals than the tested OpenAI File Search and Vectara retrieval contexts when scored with the same `ragas` stack?

It does not yet answer:

> Does HelpmateAI beat every tuned vendor deployment on arbitrary unseen documents?

Important caveats:

- The four main document families were used during HelpmateAI development and tuning.
- Vendor retrieval contexts are passed through the shared Helpmate answer generator for answer-quality comparison.
- OpenAI File Search uses `rewrite_query=True` with `max_num_results=5`.
- Vectara uses `limit=5`.
- Vendor snippets are truncated to 400 characters before shared answer generation and scoring.
- HelpmateAI uses its own final selected evidence bundle, currently `final_top_k=4`.
- The `ragas` bridge uses OpenAI-backed no-reference metrics, not human gold-answer grading.
- Abstention and partial-answer behavior can affect faithfulness and should be reported beside supported/attempted rates.

## Retrieval Snapshot

| Document | Ours hit/MRR | Vectara retrieval | OpenAI retrieval |
| --- | --- | --- | --- |
| Health policy | `0.8462 / 0.7051` | `0.7692` | `0.6923` |
| Thesis | `0.9167 / 0.5764` | `0.6667` | `0.6667` |
| `pancreas7` | `0.7778 / 0.6111` | `0.5556` | `0.3333` |
| `pancreas8` | `1.0000 / 0.8833` | `0.8000` | `0.4000` |

## Additional Report-Generation Retrieval Snapshot

Two newer paper-specific eval sets now exist for local retrieval and abstention checks:

| Document | Ours hit/MRR | Negative abstention |
| --- | --- | --- |
| `reportgeneration` | `0.9000 / 0.8500` | `1.0000` |
| `reportgeneration2` | `1.0000 / 0.8333` | `1.0000` |

Important note:

- these new paper eval sets currently measure local retrieval quality and abstention behavior
- they are useful because they confirm that broad paper-summary weakness is now more of an evidence-assembly and answer-building problem than a raw retrieval-discovery problem

## Answer-Quality Snapshot (`ragas` only)

### Health policy

| System | Faithfulness | Answer relevancy | Context precision |
| --- | --- | --- | --- |
| Ours | `0.8846` | `0.6378` | `0.8825` |
| Vectara retrieval + shared answer model | `0.7692` | `0.4504` | `0.8235` |
| OpenAI retrieval + shared answer model | `0.6154` | `0.1357` | `0.4970` |

### Thesis

| System | Faithfulness | Answer relevancy | Context precision |
| --- | --- | --- | --- |
| Ours | `1.0000` | `0.6031` | `0.8588` |
| Vectara retrieval + shared answer model | `0.8750` | `0.5579` | `0.8035` |
| OpenAI retrieval + shared answer model | `0.3702` | `0.2944` | `0.6024` |

### `pancreas7`

| System | Faithfulness | Answer relevancy | Context precision |
| --- | --- | --- | --- |
| Ours | `0.9444` | `0.6499` | `1.0000` |
| Vectara retrieval + shared answer model | `0.6111` | `0.5009` | `0.7350` |
| OpenAI retrieval + shared answer model | `0.5556` | `0.2514` | `0.6210` |

### `pancreas8`

| System | Faithfulness | Answer relevancy | Context precision |
| --- | --- | --- | --- |
| Ours | `0.9250` | `0.5527` | `0.9000` |
| Vectara retrieval + shared answer model | `0.7000` | `0.3941` | `0.6700` |
| OpenAI retrieval + shared answer model | `0.4000` | `0.1535` | `0.4422` |

## Interpretation

- Helpmate now leads both external baselines on the health-policy benchmark across all three `ragas` metrics.
- On the thesis benchmark, Helpmate remains ahead of both external baselines across all three `ragas` metrics.
- On `pancreas7`, Helpmate now has one of the strongest gains in the project, leading both external baselines clearly across all three `ragas` metrics.
- On `pancreas8`, Helpmate still leads overall across all three `ragas` metrics, though this remains the hardest paper-summary case in the benchmark mix.
- OpenAI File Search is still the weakest external baseline across the document families we tested.
- Averaged across the four main document families, Helpmate now leads:
  - Vectara by `+0.1997` faithfulness, `+0.1350` answer relevancy, and `+0.1523` context precision
  - OpenAI File Search by `+0.4532` faithfulness, `+0.4021` answer relevancy, and `+0.3697` context precision

## Lean Smart-Indexing Upgrade Check

After adding smart section profiles, orchestrated scope, selector context, and ephemeral run traces, a smaller targeted `ragas` suite was run on 2026-04-27. This suite is not a replacement for the full four-document benchmark above. It is a cost-bounded regression check focused on the new failure modes:

- thesis local chapter scope
- thesis broad synthesis
- policy claims/reimbursement
- held-out life-policy limits
- report-generation main contribution
- pancreas review broad synthesis

### Current Branch Versus `main`

The apples-to-apples regression comparison used five shared questions from the lean suite, comparing this branch against `main` before the smart index/orchestrator layer.

| System | Supported rate | Faithfulness | Answer relevancy | Context precision |
| --- | ---: | ---: | ---: | ---: |
| `main` before smart indexing/orchestration | `1.0000` | `0.8750` | `0.4964` | `0.7000` |
| Current smart-indexing branch | `1.0000` | `0.8860` | `0.5930` | `0.8000` |
| Delta | `+0.0000` | `+0.0110` | `+0.0966` | `+0.1000` |

The clearest qualitative improvement was the implementation-chapter question:

- `main` retrieved pages `90`, `23`, `72`, and `72`, drifting into final conclusion/methodology/results material
- the current branch retrieved pages `52`, `52`, `56`, and `52`, all inside the implementation chapter

### Current Branch Versus Vendors

The same six-question lean suite was also run against OpenAI File Search and Vectara retrieval, using the shared Helpmate answer generator and the same `ragas` metrics.

| System | Supported rate | Faithfulness | Answer relevancy | Context precision |
| --- | ---: | ---: | ---: | ---: |
| Helpmate smart-indexing branch | `1.0000` | `0.9050` | `0.6034` | `0.7500` |
| OpenAI File Search + shared answer model | `0.6667` | `0.9667` | `0.2676` | `0.6028` |
| Vectara + shared answer model | `0.6667` | `0.7639` | `0.3690` | `0.5556` |

Interpretation:

- OpenAI File Search scored high on faithfulness partly because it abstained or gave limited answers on weak retrieval contexts.
- Vectara answered the local thesis scope question better than OpenAI, but still mixed broader thesis pages into the evidence.
- Helpmate had the best answer relevancy, context precision, and supported rate on this targeted suite.

Reports:

- `docs/evals/reports/lean_ragas_upgrade_20260427_185007.json`
- `docs/evals/reports/lean_ragas_main_baseline_20260427_185723.json`
- `docs/evals/reports/lean_ragas_upgrade_regression_compare_20260427_1859.json`
- `docs/evals/reports/lean_vendor_ragas_upgrade_comparison_20260427_191421.json`
- `docs/evals/reports/lean_ragas_ours_vs_vendor_comparison_20260427_1915.json`

## Structure-Aware Retrieval Snapshot

Latest local topology-aware metrics:

| Document | Section hit | Region hit | Plan accuracy | Global fallback recovery | Multi-region recall |
| --- | --- | --- | --- | --- | --- |
| Health policy | `0.8462` | `0.0000` | `0.7692` | `0.0000` | `1.0000` |
| Thesis | `0.9167` | `0.7500` | `0.5000` | `0.9091` | `0.7500` |
| `pancreas7` | `0.8889` | `0.4444` | `0.6667` | `0.0000` | `0.2500` |
| `pancreas8` | `0.9000` | `0.1000` | `0.8000` | `0.0000` | `0.0000` |

Notes:

- section and region metrics are for local retrieval only
- they are meant to explain planner/topology behavior, not replace hit rate, MRR, or `ragas`
- the selector now runs in reorder-only mode with spread-only triggering by default, so it improves final evidence ordering without pruning away supporting chunks
- the dedicated `global_summary_first` route also sits inside local retrieval behavior and is intended to improve broad paper-summary evidence assembly without disturbing exact factual paths
- recent retrieval-default sweeps also refined two topology defaults:
  - `global_fallback_top_k = 3`
  - `planner_candidate_region_limit = 10`
- later repair and topology-ablation sweeps did not justify further default changes:
  - `structure_repair_confidence_threshold` stays `0.62`
  - current topology edge sets stay unchanged

## Next Step

- keep Vectara as the main external retrieval benchmark and OpenAI as a reference baseline
- treat the `2026-04-19` rerun as the current stabilized external benchmark snapshot
- only rerun the full vendor comparison again after a materially new retrieval or answer-layer architecture change
