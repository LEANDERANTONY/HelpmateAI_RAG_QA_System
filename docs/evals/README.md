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

### Retrieval-Level Comparison

| Document | Ours hit/MRR | Vectara retrieval | OpenAI retrieval |
| --- | --- | --- | --- |
| Health policy | `0.8462 / 0.7051` | `0.7692` | `0.6923` |
| Thesis | `0.9167 / 0.6042` | `0.6667` | `0.6667` |
| `pancreas7` | `0.8889 / 0.6944` | `0.5556` | `0.3333` |
| `pancreas8` | `0.9000 / 0.8000` | `0.8000` | `0.4000` |

### Answer-Quality Comparison

These scores use either our own pipeline answers or a shared answer model on top of vendor retrieval contexts.

| Document | System | Ragas faithfulness | Ragas answer relevancy | Ragas context precision |
| --- | --- | --- | --- | --- |
| Health policy | Ours | `0.8462` | `0.5995` | `0.8462` |
| Health policy | Vectara retrieval + shared answer model | `0.7692` | `0.4773` | `0.8833` |
| Health policy | OpenAI retrieval + shared answer model | `0.5769` | `0.1531` | `0.5927` |
| Thesis | Ours | `1.0000` | `0.6310` | `0.8449` |
| Thesis | Vectara retrieval + shared answer model | `0.9167` | `0.6283` | `0.8406` |
| Thesis | OpenAI retrieval + shared answer model | `0.5069` | `0.4299` | `0.5687` |
| `pancreas7` | Ours | `0.8889` | `0.5247` | `0.9599` |
| `pancreas7` | Vectara retrieval + shared answer model | `0.7778` | `0.5045` | `0.7752` |
| `pancreas7` | OpenAI retrieval + shared answer model | `0.5556` | `0.3606` | `0.4920` |
| `pancreas8` | Ours | `0.8750` | `0.5034` | `0.9222` |
| `pancreas8` | Vectara retrieval + shared answer model | `0.7667` | `0.5052` | `0.6337` |
| `pancreas8` | OpenAI retrieval + shared answer model | `0.6000` | `0.2221` | `0.4887` |

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

- add answer-quality eval coverage for the new report-generation datasets
- add optional gold-answer fields to selected datasets
- add stronger academic-synthesis eval questions
- keep Vectara as the main external benchmark and de-emphasize OpenAI in routine benchmark loops
- keep `ragas` as the only answer-quality meter in routine benchmarking
- compare against additional vendors when credentials are available:
  - Google Vertex AI Search
  - Cohere
