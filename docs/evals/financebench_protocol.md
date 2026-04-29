# FinanceBench Protocol

FinanceBench is the recommended external headline benchmark because it provides 150 human-authored, human-annotated open-source examples over public financial filings.

Source:

- GitHub: `https://github.com/patronus-ai/financebench`
- Hugging Face: `https://huggingface.co/datasets/PatronusAI/financebench`
- Paper: `https://arxiv.org/abs/2311.11944`

## Prepare Assets

Create a local FinanceBench manifest:

```powershell
uv run python -m src.evals.financebench_eval
```

This downloads:

- FinanceBench JSONL metadata into `docs/evals/financebench/`
- referenced filing PDFs into `static/financebench/`
- converted manifest into `docs/evals/financebench_manifest.json`

Those files are ignored by git because the source dataset and PDFs are reproducible from public URLs.

For a quick plumbing check:

```powershell
uv run python -m src.evals.financebench_eval --max-questions 5 --skip-pdf-download --output docs/evals/financebench_manifest.smoke.json
```

## Run

Validate:

```powershell
uv run python -m src.evals.final_eval_suite --manifest docs/evals/financebench_manifest.json --validate-only
```

Dry run:

```powershell
uv run python -m src.evals.final_eval_suite --manifest docs/evals/financebench_manifest.json --systems helpmate --max-questions 10 --skip-ragas
```

Full run:

```powershell
uv run python -m src.evals.final_eval_suite --manifest docs/evals/financebench_manifest.json --systems helpmate openai_file_search vectara
```

One-command prepare and run:

```powershell
uv run python -m src.evals.financebench_eval --run-suite --systems helpmate openai_file_search vectara
```

Lean one-command dry run:

```powershell
uv run python -m src.evals.financebench_eval --max-questions 10 --run-suite --systems helpmate --skip-ragas
```

## Judge Configuration

For the strongest public claim, use a non-OpenAI judge model for RAGAS:

```powershell
$env:HELPMATE_RAGAS_JUDGE_PROVIDER = "anthropic"
$env:HELPMATE_RAGAS_JUDGE_MODEL = "claude-3-5-sonnet-latest"
$env:ANTHROPIC_API_KEY = "..."
```

or:

```powershell
$env:HELPMATE_RAGAS_JUDGE_PROVIDER = "gemini"
$env:HELPMATE_RAGAS_JUDGE_MODEL = "gemini-2.0-flash"
$env:GOOGLE_API_KEY = "..."
```

Current note:

- The RAGAS judge LLM can be Anthropic or Gemini.
- This project still uses OpenAI embeddings for `answer_relevancy` unless `HELPMATE_RAGAS_EMBEDDING_PROVIDER` support is expanded.
- That is acceptable if documented, but a second human-reviewed subset is still better for the final README claim.

## Reporting

Report:

- overall score
- per-intent score
- false abstention rate
- attempted-only RAGAS score
- vendor deltas
- Vectara profile: `hybrid_rerank`
- OpenAI File Search config: `rewrite_query=True`, returned top-k equal to the manifest context budget
