# Final Evaluation Protocol

This protocol is for the blind evaluation we can use to make stronger claims than the current tuned project benchmark.

## Goal

Answer this question:

> On unseen long documents and frozen fresh questions, does HelpmateAI produce answers that are at least close to, and ideally better than, strong managed retrieval baselines under equalized conditions?

The suite should produce:

- overall scores
- per-intent scores
- abstention-aware scores
- side-by-side HelpmateAI, OpenAI File Search, and Vectara rows
- enough provenance that the result is auditable later

## Two Evaluation Lanes

Use both lanes if budget allows.

### Lane A: Established Benchmarks

Purpose:

- reduce criticism that we selected favorable questions
- compare against human-authored or community-recognized QA
- create the most defensible headline number

Recommended order:

1. QASPER for academic-paper QA.
2. FinanceBench for public filing QA if we want a more citable vendor-comparison benchmark.
3. LongBench v2 only if we have budget and time for a larger run.

Notes:

- QASPER aligns better with the current product story than FinanceBench.
- FinanceBench is very credible, but it may reposition the story toward financial-document QA.
- For FinanceBench, record exactly which filings were downloaded, their SEC accession/source URLs, and the subset of questions used.

### Lane B: Product-Fit Held-Out Documents

Purpose:

- test the kinds of PDFs users actually upload
- run all three systems on the exact same documents and questions
- diagnose where the architecture generalizes or fails

Recommended pilot:

- EU AI Act final text or NIST AI Risk Management Framework 1.0
- one public thesis or dissertation from a field not used in tuning
- one fresh arXiv, bioRxiv, or institutional research paper outside the tested pancreas/report-generation set
- one dense public report such as an IEA, WHO, or Federal Reserve document
- one messy layout-heavy PDF if we want a stress test

## Question Construction

Do not write questions after inspecting HelpmateAI failures.

Preferred process:

1. Choose documents and record provenance.
2. Extract full text for question-authoring only.
3. Use a third-party model family that is not the production answer model to draft questions and reference answers.
4. Review only for formatting, duplicates, and obvious unanswerable mistakes.
5. Freeze the manifest before any system run.

Target distribution per document:

| Intent type | Target share |
| --- | ---: |
| lookup | 25% |
| local_scope | 20% |
| broad_summary | 20% |
| comparison_synthesis | 15% |
| numeric_procedure | 10% |
| unsupported | 10% |

Use 25-40 questions per document for a serious product-fit run. For a pilot, 8-12 per document is acceptable.

## Manifest

The frozen manifest lives in JSON and records:

- suite id and description
- context budget
- document ids, paths, types, sources, license notes
- whether each document was used for tuning
- question ids, document ids, intent types, answerable flags
- gold answers or gold notes
- expected regions when available
- unsupported reason for negative questions

Example:

```powershell
uv run python -m src.evals.final_eval_suite --manifest docs/evals/final_eval_manifest.example.json --allow-missing-files --validate-only
```

For a real run, do not use `--allow-missing-files`.

## System Configuration

Compare:

- `helpmate`: current default stack
- `openai_file_search`: `rewrite_query=True`, equalized top-k
- `vectara`: best-effort profile with hybrid retrieval and Slingshot reranking, equalized returned top-k

Current Vectara final-eval profile:

```json
{
  "profile": "hybrid_rerank",
  "search": {
    "limit": 20,
    "lexical_interpolation": 0.025,
    "reranker": {
      "type": "customer_reranker",
      "reranker_name": "Rerank_Multilingual_v1",
      "limit": 5
    }
  }
}
```

Use `HELPMATE_VECTARA_SEARCH_PROFILE=baseline` only for historical comparisons. Do not use it for final claims.

Before the final run:

- freeze HelpmateAI code and environment variables
- freeze vendor settings
- record any vendor tuning on a development-only document
- do not tune vendor or HelpmateAI settings on the held-out final questions

## Scoring

Report at least:

- supported rate
- answerable supported rate
- false abstention rate on answerable questions
- unsupported abstention rate
- false support rate on unsupported questions
- RAGAS faithfulness
- RAGAS answer relevancy
- RAGAS no-reference context precision
- attempted-only RAGAS scores
- per-intent breakdowns
- deltas versus HelpmateAI for each external baseline

Use a second judge model family or a human-reviewed subset before broad public claims.

RAGAS judge provider can be selected with:

```powershell
$env:HELPMATE_RAGAS_JUDGE_PROVIDER = "anthropic"
$env:HELPMATE_RAGAS_JUDGE_MODEL = "claude-3-5-sonnet-latest"
```

or:

```powershell
$env:HELPMATE_RAGAS_JUDGE_PROVIDER = "gemini"
$env:HELPMATE_RAGAS_JUDGE_MODEL = "gemini-2.0-flash"
```

Claude Desktop cannot be used directly as the automated RAGAS judge; this requires an API key for the selected provider.

## Claim Gates

Tier 1:

> HelpmateAI outperforms tested vendor configurations on the current project benchmark.

Already supported by existing reports.

Tier 2:

> HelpmateAI generalizes to unseen product-fit long-document QA and is close to or better than tested managed retrieval baselines.

Requires:

- frozen held-out manifest
- at least 3 unseen document families
- per-intent reporting
- abstention-aware reporting
- equalized context budget

Tier 3:

> HelpmateAI broadly beats OpenAI File Search or Vectara.

Do not claim unless:

- we run established external benchmarks or a much larger held-out set
- vendor configurations are best-effort tuned and documented
- a second judge family or human audit agrees
- wins hold on attempted-only metrics, not only because of abstention

## Runner

Run the final suite with:

```powershell
uv run python -m src.evals.final_eval_suite --manifest docs/evals/final_eval_manifest.json --systems helpmate openai_file_search vectara
```

Lean pilot:

```powershell
uv run python -m src.evals.final_eval_suite --manifest docs/evals/final_eval_manifest.json --systems helpmate --max-questions 8 --skip-ragas
```

The runner writes reports under `docs/evals/reports/`.
