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
| Thesis | `0.9167 / 0.6042` | `0.6667` | `0.6667` |
| `pancreas7` | `0.8889 / 0.6944` | `0.5556` | `0.3333` |
| `pancreas8` | `0.9000 / 0.8000` | `0.8000` | `0.4000` |

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
| Ours | `0.8462` | `0.5995` | `0.8462` |
| Vectara retrieval + shared answer model | `0.7692` | `0.4773` | `0.8833` |
| OpenAI retrieval + shared answer model | `0.5769` | `0.1531` | `0.5927` |

### Thesis

| System | Faithfulness | Answer relevancy | Context precision |
| --- | --- | --- | --- |
| Ours | `1.0000` | `0.6310` | `0.8449` |
| Vectara retrieval + shared answer model | `0.9167` | `0.6283` | `0.8406` |
| OpenAI retrieval + shared answer model | `0.5069` | `0.4299` | `0.5687` |

### `pancreas7`

| System | Faithfulness | Answer relevancy | Context precision |
| --- | --- | --- | --- |
| Ours | `0.8889` | `0.5247` | `0.9599` |
| Vectara retrieval + shared answer model | `0.7778` | `0.5045` | `0.7752` |
| OpenAI retrieval + shared answer model | `0.5556` | `0.3606` | `0.4920` |

### `pancreas8`

| System | Faithfulness | Answer relevancy | Context precision |
| --- | --- | --- | --- |
| Ours | `0.8750` | `0.5034` | `0.9222` |
| Vectara retrieval + shared answer model | `0.7667` | `0.5052` | `0.6337` |
| OpenAI retrieval + shared answer model | `0.6000` | `0.2221` | `0.4887` |

## Interpretation

- Helpmate remains strongest overall on the health policy benchmark, with Vectara closest on context precision.
- On the thesis benchmark, Helpmate has now recovered to a stronger retrieval snapshot and remains ahead of both external baselines overall.
- On `pancreas7`, Helpmate leads both external baselines across all three `ragas` metrics.
- On `pancreas8`, Helpmate still leads overall, but broad paper-summary retrieval remains the hardest remaining case.
- OpenAI File Search is still the weakest external baseline across the document families we tested.

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

## Next Step

- keep Vectara as the main external retrieval benchmark and OpenAI as a reference baseline
- keep the current architecture stable while frontend/product work continues
- when backend work resumes, focus only on the remaining weakest broad paper-summary cases rather than another global architecture rewrite
