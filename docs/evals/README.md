# Evaluation Stack

HelpmateAI now uses a layered evaluation stack so retrieval changes can be judged from more than one angle.

## Current Evaluation Layers

- custom retrieval benchmark:
  - page-hit rate
  - mean reciprocal rank
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
| Thesis | `0.8333 / 0.5972` | not yet rerun in unified report | `0.6667` |
| `pancreas8` | `0.8 / 0.8` | `0.8` fragment match on earlier retrieval benchmark slice was stronger than OpenAI in answer eval downstream | `0.4` |

### Answer-Quality Comparison

These scores use either our own pipeline answers or a shared answer model on top of vendor retrieval contexts.

| Document | System | Ragas faithfulness | Ragas answer relevancy | Ragas context precision |
| --- | --- | --- | --- | --- |
| Health policy | Ours | `0.5923` | `0.6950` | `0.8611` |
| Health policy | Vectara retrieval + shared answer model | `0.8846` | `0.6682` | `0.8782` |
| Health policy | OpenAI retrieval + shared answer model | `0.6538` | `0.4066` | `0.3513` |
| Thesis | Ours | `0.9333` | `0.6905` | `0.8495` |
| Thesis | Vectara retrieval + shared answer model | `0.9583` | `0.6336` | `0.8875` |
| Thesis | OpenAI retrieval + shared answer model | `0.4028` | `0.2949` | `0.4528` |
| `pancreas8` | Ours | `0.7200` | `0.5996` | `0.8222` |
| `pancreas8` | Vectara retrieval + shared answer model | `0.7133` | `0.3769` | `0.6054` |
| `pancreas8` | OpenAI retrieval + shared answer model | `0.4667` | `0.3745` | `0.4578` |

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
    document_path=root / "static" / "pancreas8.pdf",
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

- add optional gold-answer fields to selected datasets
- add stronger academic-synthesis eval questions
- keep Vectara as the main external benchmark and de-emphasize OpenAI in routine benchmark loops
- keep `ragas` as the only answer-quality meter in routine benchmarking
- compare against additional vendors when credentials are available:
  - Google Vertex AI Search
  - Cohere
