# ADR-011: Partial Grounded Answers And Support Guardrail Eval

## Status

Accepted

## Context

Broad document questions exposed a weakness in the answer contract.

For questions such as section summaries, literature-review summaries, or "strongest findings" prompts, retrieval could find useful evidence while the answer layer still returned an unsupported response because the retrieved snippets did not cover every part of the user's broad wording.

We also checked whether this should be solved by tuning the weak/unsupported retrieval thresholds. A threshold sweep over the existing labeled sample corpus showed that threshold-only changes were not defensible:

- positive failures were often no-candidate retrieval failures, so thresholds could not recover them
- many negative questions still had high retrieval scores because they shared broad domain vocabulary with the document
- the current threshold set tied the best swept settings on the calibration objective

This means the weak/unsupported thresholds should remain conservative diagnostics, not the sole support decision for broad questions.

## Decision

Keep the existing weak/unsupported retrieval thresholds unchanged and treat answer-layer support verification as the final safety check.

Update the generation prompt so partial-but-grounded answers are allowed:

- if the evidence supports part of the question, answer that supported part directly
- set `supported=true` for the grounded partial answer
- explain the missing coverage in `reason`
- set `supported=false` only when the evidence cannot answer the question at all

Add a repeatable support guardrail eval:

```powershell
uv run python -m src.evals.support_guardrail_eval
```

The eval runs:

- labeled calibration positives and negatives from `docs/evals`
- held-out manual questions over `static/sample_files/test`
- retrieval status tracking alongside final answer support

## Evidence

Primary report:

- `docs/evals/reports/support_guardrail_eval_20260427_032609.json`

Result after the prompt contract change:

- calibration positives:
  - supported rate: `0.9079`
  - citation page-hit rate: `0.8158`
  - evidence fragment recall mean: `0.6719`
- calibration negatives:
  - abstention rate: `1.0000`
  - false support rate: `0.0000`
- held-out manual test-folder questions:
  - answer supported rate: `1.0000`
  - retrieval supported rate: `1.0000`
  - unsupported retrieval rate: `0.0000`

Interpretation:

- raw retrieval status alone is not enough to reject negative questions, because many negatives can still retrieve plausible-looking but off-question evidence
- answer-layer support verification is doing the real abstention work on negatives
- partial-answer prompting fixes the user-facing failure mode without weakening negative abstention

## Consequences

Positive:

- broad summary questions produce useful grounded answers instead of unnecessary "insufficient evidence" responses
- unsupported answers are still rejected when the evidence cannot answer the question at all
- the threshold choice remains defensible because we did not tune it to one document or one phrase
- future changes can rerun the support guardrail eval as a regression check

Tradeoffs:

- partial answers require clear `reason` text so users know what was missing
- answer support now depends on the structured answer model behaving consistently
- the eval should be rerun when changing retrieval routing, evidence selection, or generation prompts
