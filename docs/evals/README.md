# Evaluation Stack

HelpmateAI now uses a layered evaluation stack so retrieval changes can be judged from more than one angle.

## Current Evaluation Layers

- custom retrieval benchmark:
  - page-hit rate
  - mean reciprocal rank
- custom negative benchmark:
  - abstention rate
- external hosted baseline:
  - OpenAI file-search snippet match rate
- open-source quality benchmark:
  - `ragas` answer faithfulness
  - `ragas` answer relevancy
  - `ragas` no-reference context precision

## Why This Matters

These layers answer different questions:

- `page-hit rate` tells us whether retrieval is landing on the right document region
- `MRR` tells us how high the right evidence is ranked
- `abstention rate` tells us whether unsupported questions are rejected honestly
- `OpenAI file search` gives us an external managed-retrieval baseline
- `ragas` gives us answer-quality signals on top of retrieval, especially for broad or narrative questions

This is important because a system can retrieve the right page while still giving a vague or weak answer. That is exactly why the new `ragas` layer was added.

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
- only OpenAI hosted retrieval is currently benchmarked as an external managed baseline

## Next Eval Upgrades

- add optional gold-answer fields to selected datasets
- add stronger academic-synthesis eval questions
- compare against additional vendors when credentials are available:
  - Google Vertex AI Search
  - Cohere
  - Vectara
