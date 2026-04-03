# ADR-006: Deterministic Weak-Evidence Recovery And Guardrails

## Status

Accepted

## Context

HelpmateAI previously used model-based query rewriting as the main recovery path when retrieval evidence looked weak.

This helped occasionally, but it introduced several problems:

- rewrite behavior was variable across document families
- broad academic questions could improve on one benchmark and regress on another
- benchmark runs became harder to interpret because retrieval recovery depended on another model layer
- obviously irrelevant questions could still travel too far through the answer pipeline before failing

At the same time, the architecture already had stronger structure-aware retrieval signals available:

- query typing
- section-first routing
- section kinds
- section aliases and summaries
- metadata-aware ranking

So the rewrite layer had become a less attractive place to spend complexity.

## Decision

HelpmateAI will remove model-based query rewriting from the retrieval path.

Instead, weak-evidence recovery will use:

- deterministic query expansion
- section-aware expansion for summary-style queries
- retrieval evidence grading
- direct retrieval guardrails for clearly unsupported questions

The retrieval evidence state is now treated as:

- `strong`
- `weak`
- `unsupported`

Only the `weak` middle band can trigger adaptive recovery.

Clearly unsupported questions should short-circuit before answer generation where possible.

## Consequences

Positive:

- retrieval behavior is easier to reason about
- benchmark results are easier to trust and compare across runs
- the retrieval path has one less LLM dependency
- obviously irrelevant questions fail faster and more honestly
- broad summary recovery is now more tightly connected to document structure rather than paraphrase variability

Tradeoffs:

- deterministic expansion is less flexible than an LLM at paraphrasing unusual user wording
- some difficult broad questions still require stronger section retrieval rather than rewrite tricks
- the burden shifts toward better structure inference and section ranking quality

## Follow-On Guidance

- keep the lightweight LLM router only as a bounded route-selection aid, not as a retrieval-rewrite layer
- continue improving thesis and research-paper section retrieval through better section metadata, not by reintroducing rewrite models
- preserve the unsupported guardrail path as a product-trust feature, not only a benchmark optimization
