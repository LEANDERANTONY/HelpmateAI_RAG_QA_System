# Helpmate Evaluation Roadmap

This document captures the evaluation suite we intend to build for HelpmateAI so we can justify each major layer of the system with evidence rather than intuition.

The goal is not only to know whether the app works end to end, but also to know which internal layers are earning their place in the architecture.

## Why We Need This

Helpmate is not a single-model system. It is a pipeline with many interacting parts:

- document ingestion and parsing
- section building
- chunking
- synopsis generation
- topology generation
- query analysis
- planner / route-selection LLM
- hybrid retrieval
- reranking
- evidence selection
- answer generation
- unsupported-answer guardrails

If we only measure the final answer, we cannot tell:

- which component is helping
- which component is redundant
- which component is actively hurting
- whether a component's quality gain is worth its latency, cost, or complexity

So the correct evaluation design for this project is a layered eval stack plus ablation testing.

## Core Evaluation Principle

Every major layer should eventually answer four questions:

1. Does it improve quality?
2. Does it improve safety or grounding?
3. Does it improve the user-visible answer?
4. Is the gain worth the complexity, runtime, and cost?

If a layer cannot justify itself against those questions, it should be reconsidered.

## Evaluation Layers

### 1. Ingestion And Structure Eval

Purpose:
- validate the quality of the document representation before retrieval even begins

What this layer covers:
- parsing accuracy
- section boundary quality
- section title quality
- chunk coverage
- chunk redundancy
- synopsis usefulness
- topology usefulness

Questions this layer should answer:
- are we preserving the right content from the source document?
- do chunks cover the evidence fragments that matter?
- are sections and synopses coherent enough to support later routing and retrieval?

Candidate metrics:
- expected-fragment coverage by chunks
- average chunk redundancy / overlap waste
- section-title match quality
- page-label preservation quality
- synopsis-to-section alignment quality

Why it matters:
- if the structural representation is weak, all later retrieval and answer metrics are partly misleading

### 2. Planner / Routing Eval

Purpose:
- test whether the query-analysis and route-selection logic is actually making correct retrieval decisions

What this layer covers:
- query analysis
- planner intent detection
- evidence spread prediction
- route selection
- confidence behavior

Questions this layer should answer:
- when should the system use chunk-first vs synopsis-first vs hybrid?
- is the planner making better decisions than a deterministic baseline?
- is the planner confidence calibrated?

Candidate metrics:
- intent-label accuracy
- route accuracy
- evidence-spread accuracy
- planner confidence calibration
- fallback recovery rate

Why it matters:
- the planner adds complexity and LLM cost, so we need to prove it improves retrieval behavior enough to justify itself

### 3. Retrieval Eval

Purpose:
- measure whether the system reaches the correct evidence region in the document

What this layer covers:
- dense retrieval
- lexical retrieval
- fusion
- section retrieval
- synopsis retrieval
- topology-guided retrieval

Current repo support:
- this is the strongest existing part of the eval stack

Current metrics already in use:
- page-hit rate
- mean reciprocal rank
- section-hit rate
- region-hit rate
- planner/topology diagnostics

Metrics we should continue using:
- page-hit rate
- MRR
- fragment recall
- section-hit rate
- region-hit rate
- plan accuracy

Why it matters:
- retrieval is the core grounding layer; if retrieval is weak, downstream improvements are often cosmetic

### 4. Reranker Eval

Purpose:
- isolate whether reranking improves candidate ordering enough to justify its compute and memory cost

What this layer covers:
- candidate reordering after retrieval

Questions this layer should answer:
- does reranking improve top-k evidence quality?
- does it improve page hit, MRR, or fragment recall?
- does it improve final answer quality enough to justify its runtime and RAM overhead?

Candidate metrics:
- delta in page-hit rate before and after reranking
- delta in MRR before and after reranking
- delta in fragment recall
- latency overhead
- memory overhead

Primary ablation:
- reranker off vs reranker on

Why it matters:
- reranking is one of the more expensive layers in the system, so it must justify itself empirically

### 5. Evidence Selector Eval

Purpose:
- measure whether the selector improves the final evidence set beyond retrieval and reranking alone

What this layer covers:
- final evidence disambiguation over shortlisted candidates

Questions this layer should answer:
- does the selector improve evidence precision?
- does it improve fragment recall?
- does it reduce poor evidence choices?
- does it improve answer faithfulness downstream?

Candidate metrics:
- page-hit delta before vs after selection
- fragment-recall delta before vs after selection
- bad reshuffle rate
- evidence precision

Primary ablations:
- selector off vs selector on
- selector weight sweep within selector-on mode

Why it matters:
- the selector adds an extra LLM call, so it should improve evidence quality enough to justify that cost

### 6. Answer-Generation Eval

Purpose:
- measure the quality of the final answer users actually see

What this layer covers:
- answer generation
- evidence-to-answer grounding
- citation behavior
- abstention behavior

Questions this layer should answer:
- are answers faithful to the retrieved evidence?
- are citations actually useful?
- does the model abstain correctly on unsupported questions?

Candidate metrics:
- faithfulness
- answer relevancy
- context precision
- citation usefulness
- supported vs unsupported accuracy
- false-support rate
- abstention rate

Why it matters:
- the final user experience is at the answer layer, not the retrieval layer

### 7. Cost / Latency / Operational Eval

Purpose:
- evaluate whether each architectural layer is worth its practical cost

What this layer covers:
- runtime
- token cost
- memory usage
- indexing latency
- answer latency

Questions this layer should answer:
- does a quality gain justify extra cost?
- which layers are most expensive?
- which layers are the best trade-offs?

Candidate metrics:
- indexing wall-clock time
- retrieval latency
- answer latency
- token cost
- peak memory usage

Why it matters:
- Helpmate has real deployment constraints, so quality alone is not enough

## Ablation Strategy

The correct way to justify the stack is not just by showing one good benchmark result.

We need ablation-style comparisons where only one layer changes at a time.

Recommended ablation families:

### Retrieval Stack Ablations

- chunk-first only
- synopsis-first only
- hybrid retrieval

### Planner Ablations

- deterministic routing only
- planner-enabled routing

### Reranker Ablations

- reranker off
- reranker on

### Selector Ablations

- selector off
- selector on
- selector on with tuned weights

### Answer-Layer Ablations

- answer generation with baseline evidence
- answer generation with reranked evidence
- answer generation with reranked + selected evidence

## Proposed Evaluation Stack

We should build and run the suite in this order:

1. retrieval benchmark
2. selector on/off benchmark
3. reranker on/off benchmark
4. planner on/off benchmark
5. negative / abstention benchmark
6. answer-quality benchmark
7. cost / latency benchmark
8. weight sweeps for tunable subsystems

This order matters because:

- retrieval must be stable before downstream evaluations are trustworthy
- selector and reranker are easier to isolate than planner
- answer-quality eval is slower and more expensive, so it should come after structural and retrieval-level checks

## Immediate Next Steps

The first useful ablations to build are:

1. selector off vs selector on
2. reranker off vs reranker on

Reason:

- both layers are important
- both add runtime cost
- both are relatively easy to isolate with the current architecture
- both affect evidence quality before final answer generation

After that, the next step should be:

3. planner-enabled vs deterministic routing

That will tell us whether the query-analysis / planner LLM is truly earning its place.

## Current Decision On Evidence Selector Weights

We already completed the first tuning pass for evidence-selector blend weights.

What was done:

- built an offline sweep over the labeled retrieval datasets
- used an objective combining:
  - page-hit rate
  - fragment recall
  - MRR
- found that the best-performing region was a broad plateau rather than a narrow optimum

Current tuned default:

- `rank_weight = 0.25`
- `llm_weight = 0.75`

Reason we chose this:

- it sits inside the best-performing plateau
- it slightly improves aggregate evidence quality over the earlier hand-set default
- it keeps a retrieval prior in the loop instead of making the selector purely LLM-driven

This is a good example of how we want the whole eval stack to work:

- propose a layer
- isolate it
- measure it
- justify its final setting with evidence

## Current Calibration And Ablation Findings

These findings should be treated as the current architecture record until future evals overturn them.

### 1. Evidence Selector

What was measured:

- weight sweep over labeled retrieval datasets
- selector off vs selector on evidence-level ablation

What we learned:

- the selector-weight blend itself can be tuned and the best region is a broad plateau
- a tuned blend of `rank_weight=0.25` and `llm_weight=0.75` is a reasonable internal default inside that plateau
- however, the selector-on evidence ablation currently underperforms selector-off on the current retrieval benchmark

Current interpretation:

- the selector is not yet justified at the evidence-selection layer
- it may still help final answer quality, but that must be proven in a later answer-layer eval

### 2. Reranker

What was measured:

- reranker off vs reranker on ablation with selector disabled

What we learned:

- reranker-on produced a strong improvement in page-hit rate, MRR, and fragment recall across most document families
- only one dataset was slightly worse, while the broader result was strongly positive

Current interpretation:

- the reranker is justified by the current retrieval-level evidence

### 3. Planner Threshold Calibration

What was measured:

- sweep over `HELPMATE_PLANNER_CONFIDENCE_THRESHOLD`
- sweep over `HELPMATE_ROUTER_CONFIDENCE_THRESHOLD`
- ranking of threshold pairs by raw retrieval metrics first:
  - page-hit rate
  - fragment recall
  - MRR

Best calibrated pair from the current sweep:

- `HELPMATE_PLANNER_CONFIDENCE_THRESHOLD=0.70`
- `HELPMATE_ROUTER_CONFIDENCE_THRESHOLD=0.62`

What we learned:

- the previous defaults were close, but slightly suboptimal
- the calibrated pair increases fallback usage in the lower-confidence planner cases without damaging the stronger planner cases
- the result slightly improves retrieval quality while giving the planner/router layer a more defensible gate

Current interpretation:

- planner usefulness should be judged only after using the calibrated threshold pair above
- the next planner ablation should compare:
  - calibrated planner/router behavior
  - deterministic-only routing

### 4. Planner Ablation

What was measured:

- deterministic-only planner behavior
- calibrated planner/router behavior using:
  - `HELPMATE_PLANNER_CONFIDENCE_THRESHOLD=0.70`
  - `HELPMATE_ROUTER_CONFIDENCE_THRESHOLD=0.62`

What we learned:

- calibrated planner/router behavior produced the same page-hit rate and MRR as deterministic-only routing
- it produced a small improvement in fragment recall
- the LLM fallback activated on `12` low-confidence cases
- only `1` question improved, `0` worsened, and `75` were unchanged

Current interpretation:

- the planner/router layer is not strongly harmful
- the planner/router layer is not yet strongly justified by a large benchmark gain either
- at the retrieval layer, the planner currently looks like a small targeted improvement rather than a major architecture win
- the final answer-layer eval is important, because planner value may show up more clearly in answer quality than in retrieval-level metrics alone

### 5. Answer-Layer Stack Comparison

What was measured:

- `baseline`
  - deterministic routing
  - reranker off
  - selector off
- `reranker_only`
  - deterministic routing
  - reranker on
  - selector off
- `planner_reranker`
  - calibrated planner/router
  - reranker on
  - selector off
- `full_stack`
  - calibrated planner/router
  - reranker on
  - selector on

Positive answer-layer metrics used:

- supported rate on positive datasets
- citation page-hit rate
- evidence fragment recall mean

Negative answer-layer metrics used:

- abstention rate
- false-support rate

What we learned:

- reranker materially improves answer-layer quality
- planner adds a small further gain on positive answer-layer evidence quality
- selector reduces answer-layer quality on the current benchmark
- none of the tested variants changed the current negative-set abstention behavior

Current interpretation:

- reranker should remain part of the architecture
- planner/router can remain, but should be treated as a modest incremental improvement rather than a dramatic win
- selector should not currently be treated as justified by the benchmark evidence

### 6. Focused External `ragas` Cross-Check

What was measured:

- focused `ragas` comparison across three representative document families:
  - health policy
  - thesis
  - `pancreas7`
- stack variants:
  - `reranker_only`
  - `planner_reranker`
  - `full_stack`

Metrics:

- faithfulness
- answer relevancy
- no-reference context precision
- supported rate

What we learned:

- `planner_reranker` produced the strongest faithfulness score in the external check
- `full_stack` produced the strongest context precision and answer relevancy
- however, `full_stack` did so with a noticeable drop in faithfulness compared with both `reranker_only` and `planner_reranker`
- this means the selector is not uniformly bad, but its current effect is still a tradeoff rather than a net positive

Current interpretation:

- the external check does not overturn the internal benchmark conclusion
- it does clarify the selector's current profile:
  - better on narrow context precision
  - worse on answer faithfulness
- for Helpmate, faithfulness remains the more important production criterion, so the selector still does not currently justify itself as the default path

## Guiding Rule Going Forward

For every major Helpmate layer, we should be able to say:

- what it is supposed to improve
- how we measure that improvement
- what its ablation result looks like
- what trade-offs it introduces

If we cannot say that clearly, the layer is not yet justified enough.
