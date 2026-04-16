# ADR-008: Benchmark-Driven Stack Defaults And Experimental Selector

## Status

Accepted

## Context

By the time HelpmateAI had document-topology planning, bounded evidence selection, and a lightweight planner/router fallback, the architecture had become strong enough that intuition alone was no longer a safe basis for keeping or removing layers.

Several parts of the stack were plausible in theory:

- reranking after retrieval
- low-confidence planner/router fallback
- post-rerank evidence selection

But they did not all have the same cost profile:

- reranker adds retrieval-time compute and memory pressure
- planner/router adds occasional extra LLM calls
- evidence selector adds another answer-path LLM step and more latency

The project therefore needed a benchmark-driven architecture decision, not just another implementation pass.

## Decision

We evaluated the major post-retrieval architecture layers with a layered benchmark stack and chose the default interpretation below.

### 1. Keep reranker as a core default layer

Reason:

- reranker delivered the clearest improvement on retrieval metrics
- reranker also delivered the clearest improvement on answer-layer quality
- the latency increase was moderate relative to the quality gain

### 2. Keep planner/router fallback as a modest default layer

Reason:

- planner/router thresholds were calibrated before ablation
- planner/router produced a small but consistent improvement
- the fallback only activates on a minority of low-confidence cases

This layer should be treated as a targeted improvement, not as the main source of quality.

### 3. Treat evidence selector as experimental, not currently production-default

Reason:

- selector weights can be tuned internally
- however, selector-on underperformed selector-off on the current evidence benchmark
- selector-on also underperformed the stronger non-selector stacks on the answer benchmark
- the focused external `ragas` cross-check showed selector tradeoffs, not a decisive win
- selector also added the largest answer-path latency and extra LLM-stage usage

### 4. Keep the evaluation stack as part of architecture, not just testing

The following eval surfaces are now part of the decision-making architecture:

- selector weight sweep
- selector on/off ablation
- reranker on/off ablation
- planner threshold calibration
- planner ablation
- answer-stack ablation
- latency/cost benchmark
- focused external `ragas` stack comparison

## Consequences

Positive:

- architecture choices now have an explicit evidence trail
- the stack can be discussed in terms of quality, safety, and cost rather than taste
- future additions can be held to the same ablation standard

Tradeoffs:

- some implemented features may remain in the repo even when they are not justified as defaults
- benchmark maintenance is now part of the product engineering burden
- eval reports become part of the architecture record, not just temporary experiments

## Rejected Alternatives

- keeping all layers active because they were conceptually appealing
- deciding based only on a single end-to-end benchmark
- tuning selector weights and assuming that alone justified the selector
- judging planner value before calibrating its thresholds

## Notes

This ADR does not erase ADR-007.

Instead, it refines how ADR-007 should be operationalized:

- document-topology planning remains part of the stack
- bounded evidence selection remains available in the codebase
- but the bounded evidence selector is now treated as an experimental layer until future evals justify default use

Primary supporting documents:

- `docs/evals/EVAL_ROADMAP.md`
- `docs/evals/ARCHITECTURE_SCORECARD.md`
- `docs/evals/reports/`
