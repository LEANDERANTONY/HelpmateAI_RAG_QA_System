# Evaluation Stack

HelpmateAI now uses a layered evaluation stack so retrieval changes can be judged from more than one angle.

## Current Evaluation Layers

- custom retrieval benchmark:
  - page-hit rate
  - mean reciprocal rank
  - structure-aware planner/topology metrics
- custom negative benchmark:
  - abstention rate
- external hosted baselines:
  - Vectara retrieval snippet match rate
  - OpenAI file-search snippet match rate
- open-source quality benchmark:
  - `ragas` answer faithfulness
  - `ragas` answer relevancy
  - `ragas` no-reference context precision
- shared-answer quality benchmark:
  - `ragas` scoring on top of OpenAI retrieval contexts
  - `ragas` scoring on top of Vectara retrieval contexts

## Why This Matters

These layers answer different questions:

- `page-hit rate` tells us whether retrieval is landing on the right document region
- `MRR` tells us how high the right evidence is ranked
- `abstention rate` tells us whether unsupported questions are rejected honestly
- `Vectara retrieval` gives us the strongest current external managed-retrieval baseline
- `OpenAI file search` gives us a historical convenience baseline
- `ragas` gives us answer-quality signals on top of retrieval, especially for broad or narrative questions
This is important because a system can retrieve the right page while still give a vague or weak answer. That is exactly why the `ragas` layer is now the main answer-quality signal.

## Current Summary Table

As of the stabilized `2026-04-19` snapshot, the repo treats these tables as the current reference view of the benchmark stack.

### Retrieval-Level Comparison

| Document | Ours hit/MRR | Vectara retrieval | OpenAI retrieval |
| --- | --- | --- | --- |
| Health policy | `0.8462 / 0.7051` | `0.7692` | `0.6923` |
| Thesis | `0.9167 / 0.5764` | `0.6667` | `0.6667` |
| `pancreas7` | `0.7778 / 0.6111` | `0.5556` | `0.3333` |
| `pancreas8` | `1.0000 / 0.8833` | `0.8000` | `0.4000` |

### Answer-Quality Comparison

These scores use either our own pipeline answers or a shared answer model on top of vendor retrieval contexts.

| Document | System | Ragas faithfulness | Ragas answer relevancy | Ragas context precision |
| --- | --- | --- | --- | --- |
| Health policy | Ours | `0.8846` | `0.6378` | `0.8825` |
| Health policy | Vectara retrieval + shared answer model | `0.7692` | `0.4504` | `0.8235` |
| Health policy | OpenAI retrieval + shared answer model | `0.6154` | `0.1357` | `0.4970` |
| Thesis | Ours | `1.0000` | `0.6031` | `0.8588` |
| Thesis | Vectara retrieval + shared answer model | `0.8750` | `0.5579` | `0.8035` |
| Thesis | OpenAI retrieval + shared answer model | `0.3702` | `0.2944` | `0.6024` |
| `pancreas7` | Ours | `0.9444` | `0.6499` | `1.0000` |
| `pancreas7` | Vectara retrieval + shared answer model | `0.6111` | `0.5009` | `0.7350` |
| `pancreas7` | OpenAI retrieval + shared answer model | `0.5556` | `0.2514` | `0.6210` |
| `pancreas8` | Ours | `0.9250` | `0.5527` | `0.9000` |
| `pancreas8` | Vectara retrieval + shared answer model | `0.7000` | `0.3941` | `0.6700` |
| `pancreas8` | OpenAI retrieval + shared answer model | `0.4000` | `0.1535` | `0.4422` |

Average current margin:

- versus `Vectara`: `+0.1997` faithfulness, `+0.1350` answer relevancy, `+0.1523` context precision
- versus `OpenAI File Search`: `+0.4532` faithfulness, `+0.4021` answer relevancy, `+0.3697` context precision

## Structure-Aware Metrics

The current local retrieval stack also emits planner/topology metrics:

- `section_hit_rate`
- `region_hit_rate`
- `plan_accuracy`
- `global_fallback_recovery_rate`
- `multi_region_recall`

These are diagnostic metrics for the local architecture, not vendor-comparison metrics.

## New Report-Generation Eval Sets

Two additional journal-paper eval sets are now included:

- `reportgeneration`
- `reportgeneration2`

Current local retrieval snapshot on those datasets:

| Document | Ours hit/MRR | Negative abstention |
| --- | --- | --- |
| `reportgeneration` | `0.9000 / 0.8500` | `1.0000` |
| `reportgeneration2` | `1.0000 / 0.8333` | `1.0000` |

Interpretation:

- retrieval on these papers is already healthy
- the remaining challenge is the broadest paper-summary phrasing, not raw evidence discovery
- that is why the latest architecture work focused on a dedicated global-summary evidence route instead of another retrieval rewrite

## Evidence-Selector Calibration

The evidence selector combines:

- a retrieval prior from fused retrieval ranking
- an LLM score over the shortlisted candidates

Those blend weights are treated as hyperparameters rather than fixed intuition.

The repo now includes an offline sweep script:

```powershell
uv run python -m src.evals.evidence_selector_weight_sweep --step 0.01
```

That sweep caches the selector model's candidate scores once, then replays the same questions offline across different rank/LLM mixes using the labeled retrieval datasets already in this repo.

Current tuning snapshot:

- benchmark size: `76` labeled questions across health policy, thesis, pancreas, and report-generation datasets
- objective: `0.45 * page_hit_rate + 0.35 * fragment_recall + 0.20 * MRR`
- best-performing plateau: `rank_weight` from about `0.03` to `0.37`
- chosen default: `rank_weight=0.25`, `llm_weight=0.75`

Why that default was chosen:

- it sits inside the top-performing plateau instead of at a fragile edge
- it keeps the LLM score as the dominant signal when the selector is invoked
- it still preserves a meaningful retrieval prior for tie-breaking and stability
- it outperforms the older `0.65 / 0.35` hand-set blend on fragment recall while keeping the same page-hit and MRR on the current benchmark

Current production selector policy:

- `reorder-only`, not prune mode
- `spread-only` trigger policy
- `weak_evidence=false`
- `ambiguity=false`

Why:

- the selector's original regression came from pruning away support, not from reordering itself
- spread-only gives the best current production tradeoff between answer quality and activation frequency
- always-on remains a useful reference mode, but not the default shipping policy

## Support Guardrail Eval

The weak/unsupported retrieval thresholds are now checked separately from final answer support.

Run the support guardrail eval with:

```powershell
uv run python -m src.evals.support_guardrail_eval
```

The eval covers:

- labeled calibration positives and negatives from `docs/evals`
- held-out manual questions over `static/sample_files/test`
- retrieval status distribution
- final answer supported/abstained behavior

Current report:

- `docs/evals/reports/support_guardrail_eval_20260427_032609.json`

Current result:

- calibration positive supported rate: `0.9079`
- calibration negative abstention rate: `1.0000`
- calibration false support rate: `0.0000`
- held-out answer supported rate: `1.0000`
- held-out unsupported retrieval rate: `0.0000`

Decision from this run:

- keep weak/unsupported thresholds unchanged
- use answer-layer support verification as the final safety layer
- allow partial but grounded answers when retrieved evidence answers only part of a broad question

## Baseline Policy

Going forward, Vectara should be treated as the primary external managed-retrieval benchmark.

Reason:

- it is consistently stronger than OpenAI File Search on the current document families
- it is the more demanding and useful comparison point for Helpmate now

OpenAI File Search should remain in the repo as a historical/reference benchmark, but it no longer needs to be the default benchmark we optimize against.

## Current Command Surfaces

- run the existing benchmark comparison:

```powershell
uv run python -m src.evals.compare_benchmarks
```

- run a document-specific comparison from Python:

```python
from pathlib import Path
from src.evals.compare_benchmarks import compare

root = Path(".").resolve()
report = compare(
    document_path=root / "static" / "sample_files" / "pancreas8.pdf",
    positive_dataset_path=root / "docs" / "evals" / "pancreas8_retrieval_eval_dataset.json",
    negative_dataset_path=root / "docs" / "evals" / "pancreas8_negative_eval_dataset.json",
)
```

Reports are saved under `docs/evals/reports/`.

## Current Limitations

- `ragas` currently uses OpenAI-backed evaluation models, so it is still not a zero-cost eval layer
- the current `ragas` bridge uses no-reference metrics because our datasets are retrieval-labeled, not gold-answer datasets
- Vectara retrieval is the primary external baseline, while OpenAI remains a historical/reference baseline
- Vectara factual-consistency is no longer part of the active decision-making benchmark stack because it was too sensitive to answer formatting for our current usage

## Next Eval Upgrades

- add answer-quality eval coverage for the newer report-generation datasets
- add optional gold-answer fields to selected datasets
- add stronger academic-synthesis eval questions
- keep Vectara as the main external benchmark and OpenAI as a reference baseline in routine benchmark loops
- keep `ragas` as the main answer-quality meter in routine benchmarking
- compare against additional vendors when credentials are available:
  - Google Vertex AI Search
  - Cohere
