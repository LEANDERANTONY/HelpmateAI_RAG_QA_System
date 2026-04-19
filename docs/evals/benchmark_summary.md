# Benchmark Summary

This file is the current high-level benchmark snapshot for HelpmateAI.

## Policy

- use `ragas` as the only answer-quality evaluation meter going forward
- keep `Vectara` as the main external retrieval baseline
- keep `OpenAI File Search` as a historical/reference retrieval baseline only
- do not use Vectara factual-consistency as a decision-making metric in routine benchmarking

Reason:

- `ragas` is more interpretable for the kinds of answer-quality tradeoffs we care about
- Vectara factual-consistency produced unstable or formatting-sensitive readings for our answer style
- Vectara retrieval is the strongest external retrieval baseline we have tested so far

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
