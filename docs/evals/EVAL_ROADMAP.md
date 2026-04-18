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

- the original prune-based selector was not justified at the evidence-selection layer
- that result turned out to conflate two effects:
  - evidence reordering
  - evidence pruning
- the later reorder-only experiment isolated those effects and overturned the selector conclusion

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
- the prune-based selector should not be used as the default selector design
- however, this answer-layer comparison did not yet isolate prune vs reorder-only behavior

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

- the original focused external check used the prune-based selector behavior
- that run was useful, but it did not isolate whether the regression came from reordering itself or from shrinking the evidence set

Current interpretation:

- this external check should now be treated as historical context for the prune-based selector only
- the controlling selector decision must instead follow the later isolate-pruning experiment

### 7. Selector Reorder-Only Follow-Up

What was measured:

- retrieval-only compare on the same cached selector cases:
  - selector off
  - selector prune
  - selector reorder-only
- answer-layer compare on the same positive and negative datasets
- focused `ragas` compare on the same three representative document families:
  - health policy
  - thesis
  - `pancreas7`

Reports:

- `docs/evals/reports/evidence_selector_mode_compare_20260418_164811.json`
- `docs/evals/reports/selector_answer_mode_compare_20260418_170916.json`
- `docs/evals/reports/selector_ragas_mode_compare_20260418_184714.json`

What we learned:

- pruning was the source of the earlier selector regression
- retrieval objective:
  - selector off `0.7674`
  - selector prune `0.7255`
  - selector reorder-only `0.7757`
- answer-layer supported rate:
  - planner+rereanker `0.8421`
  - selector prune `0.8289`
  - selector reorder-only `0.8553`
- focused `ragas`:
  - planner+rereanker:
    - faithfulness `0.9310`
    - answer relevancy `0.6555`
    - context precision `0.9036`
  - selector reorder-only:
    - faithfulness `0.9657`
    - answer relevancy `0.6436`
    - context precision `0.9608`

Selector gate usage on the benchmark:

- positive eval set: `68 / 76` = `89.5%`
- focused `ragas` subset: `32 / 34` = `94.1%`

Current interpretation:

- the selector is now justified again, but only in reorder-only mode
- the earlier negative selector conclusion should be preserved as history for prune mode, not carried forward as the final selector verdict
- this is now a benchmark-backed architecture change, not an intuition change

### 8. Selector Calibration Follow-Up

What was measured:

- reorder-only weight sweep
- gap-threshold sweep
- threshold answer-layer compare
- threshold focused `ragas` compare
- trigger-source isolation on retrieval, answer-layer, and focused `ragas`

What we learned:

- reorder-only weight tuning is a broad plateau
  - the current `0.25 / 0.75` blend remains valid
- ambiguity-triggered selection is not the best production default
  - threshold sweeps tied on retrieval objective, but ambiguity-heavy modes did not dominate once answer quality and trigger-rate cost were included
- weak-evidence-only triggering is effectively inactive on the current benchmark
- the real production tradeoff is:
  - `spread_only` for lower trigger rate and strong answer quality
  - `always_on` for maximum grounding at maximum selector cost

Current interpretation:

- the selector story is now complete enough to set a production trigger policy
- the recommended production default is:
  - reorder-only selector
  - `rank_weight = 0.25`
  - `llm_weight = 0.75`
  - spread-only triggering

### 9. Retrieval-Default Sweeps

What was measured:

- synopsis section-window sweep
- synopsis dense/lexical/fused top-k grid
- global fallback top-k sweep
- planner candidate-region-limit sweep

What we learned:

- `synopsis_section_window = 4` remains the best current default
- the tested synopsis top-k grid was effectively flat, so the current `8 / 8 / 5` pool remains justified
- `global_fallback_top_k = 3` slightly outperformed the current `4`
- `planner_candidate_region_limit = 10` clearly outperformed the current `6` on overall objective and plan accuracy

Current interpretation:

- the topology-aware retrieval defaults are now much less intuition-driven than before
- the next remaining unvalidated retrieval-side questions are mostly:
  - structure-repair calibration
  - topology edge-type ablation

## Guiding Rule Going Forward

For every major Helpmate layer, we should be able to say:

- what it is supposed to improve
- how we measure that improvement
- what its ablation result looks like
- what trade-offs it introduces

If we cannot say that clearly, the layer is not yet justified enough.

## Future Product Focus

Now that the deployed stack is stable, the roadmap should shift from bring-up work to deeper quality justification and product robustness.

### 1. Expand The Eval Corpus

- add more real user-style PDFs and DOCX files
- include noisier layouts, longer reports, and more mixed-structure documents
- grow the negative / unsupported question set

Why this matters:

- the current eval corpus is strong enough for first architecture decisions
- but broader coverage will reduce the risk of overfitting those decisions to a small document family mix

### 2. Benchmark Chunking More Rigorously

Current state:

- chunking behavior is deterministic and covered by tests
- the repo has a small retrieval `grid_search.py` helper
- the live chunking defaults were originally `chunk_size=1200` and `chunk_overlap=180`
- after the full retrieval, answer-layer, and focused `ragas` pass, the recommended production overlap is now `240`

Future work:

- run a documented chunk-size / overlap sweep across the labeled eval sets
- compare settings on:
  - page-hit rate
  - fragment recall
  - answer faithfulness
  - indexing latency
  - storage footprint
- test whether document-style-specific chunking rules outperform a single global default

### 3. Benchmark Alternative Reranker Models

Current state:

- we proved that reranking as a layer is valuable
- we did not yet prove that `cross-encoder/ms-marco-MiniLM-L-6-v2` was the best reranker choice for Helpmate

Future work:

- compare the current reranker against a shortlist of alternatives
- measure:
  - retrieval metrics
  - answer-layer faithfulness
- latency
- memory usage
- VPS deployment fit

Update:

- we now completed a first reranker model comparison across the official MS MARCO cross-encoder family
- models compared:
  - `cross-encoder/ms-marco-TinyBERT-L2-v2`
  - `cross-encoder/ms-marco-MiniLM-L6-v2`
  - `cross-encoder/ms-marco-MiniLM-L12-v2`

What we learned:

- retrieval-only sweep favored `MiniLM-L12-v2`
- however, the answer-layer comparison and focused `ragas` cross-check both favored keeping `MiniLM-L6-v2`
- `L12` improved retrieval ordering, but that did not translate into a better end-to-end answer profile on the current benchmark

Current interpretation:

- `cross-encoder/ms-marco-MiniLM-L6-v2` remains the best current production reranker choice
- `MiniLM-L12-v2` is a strong retrieval candidate, but not a justified production replacement yet
- this is now a benchmark-backed choice rather than an arbitrary default

### 4. Benchmark Alternative Embedding Models

Current state:

- the app uses `text-embedding-3-small`
- that is a sensible cost-quality default
- but it has not yet been justified by a Helpmate-specific model comparison

Future work:

- compare candidate embedding models on:
  - retrieval quality
  - indexing speed
  - storage footprint
  - cost

### 5. Strengthen Answer-Layer Judging

- expand external `ragas` evaluation beyond the current focused subset
- add a small human-reviewed benchmark slice
- track:
  - faithfulness
  - citation usefulness
  - completeness
  - abstention quality

### 6. Operational Hardening

- add monitoring around upload failures, indexing failures, QA latency, and cleanup health
- keep auditing VPS disk usage and retention cleanup behavior
- add lightweight diagnostics if product usage grows

## Important Open Question: Were Chunking And Reranker Model Choices Arbitrary?

Short answer:

- partly, yes

More precise answer:

- the current chunking parameters and reranker model were chosen as sensible engineering defaults
- the architecture around them has now been benchmarked
- but the internal model / hyperparameter choices themselves have not yet gone through the same level of systematic comparison that we applied to selector weights and planner thresholds

What is already justified:

- reranker as a layer
- planner thresholds
- selector weights

What is not yet fully justified:

- chunk size / overlap as final values
- embedding model identity

So the next maturity step for Helpmate is not to make the app merely work.

It is to turn the remaining strong defaults into benchmark-backed decisions.

## First-Pass Chunking Sweep Finding

We completed a first retrieval-level chunking sweep after fixing an index-cache bug that had previously allowed chunking experiments to reuse stale indexes by fingerprint alone.

What was fixed first:

- index reuse now requires matching:
  - schema version
  - embedding model
  - chunk size
  - chunk overlap

What was then measured:

- a focused chunking comparison across the full labeled retrieval corpus
- candidate settings:
  - `900 / 180`
  - `1200 / 180`
  - `1200 / 240`
  - `1500 / 180`

Current evaluated baseline:

- `chunk_size = 1200`
- `chunk_overlap = 180`

First-pass result:

- `1200 / 240` ranked best on retrieval-level page hit and section hit
- the gain over `1200 / 180` was modest, not dramatic
- some document families improved
- `pancreas7` regressed in this pass

Current interpretation:

- `1200 / 240` emerged as the strongest next candidate from the retrieval sweep
- retrieval-only evidence was not enough to flip production by itself
- this configuration needed answer-layer and external validation before promotion

## Chunking Follow-Up: Answer-Layer And `ragas` Cross-Checks

We then ran the more important follow-up comparisons with the rest of the stack held constant:

- planner/router on
- reranker on
- selector off

The chunking variants compared were:

- `1200 / 180`
- `1200 / 240`

### Internal Answer-Layer Result

What was measured:

- supported rate on positive datasets
- citation page-hit rate
- evidence fragment recall
- abstention rate on the negative set

What we learned:

- `1200 / 180` performed better on positive answer support
- `1200 / 180` also kept a better evidence fragment recall profile
- `1200 / 240` slightly improved the negative-set abstention result
- but that gain came with weaker positive answer quality on the current benchmark

Current interpretation:

- the internal answer-layer check was the main argument for staying conservative with `1200 / 180`
- however, this was only one part of the final decision and needed to be weighed against the external `ragas` signal

### Focused External `ragas` Result

What was measured:

- a focused `ragas` comparison on three representative document families:
  - health
  - thesis
  - `pancreas7`
- metrics:
  - supported rate
  - faithfulness
  - answer relevancy
  - context precision

What we learned:

- `1200 / 240` scored better on:
  - supported rate
  - answer relevancy
  - context precision
- `1200 / 180` scored slightly better on faithfulness
- the faithfulness gap was small overall, but for Helpmate it still matters more than cosmetic answer improvement

Current interpretation:

- the external check confirms the same tradeoff pattern:
  - broader overlap helps answer directness and context precision
  - faithfulness decreases slightly, but only marginally on the focused subset
- taken together, the retrieval sweep plus the external check justify promoting `1200 / 240` as the production default
- this is a product choice:
  - accept a very small grounding dip
  - in exchange for noticeably stronger answer relevancy and context precision
- `1200 / 180` remains the more conservative fallback if future datasets show a stronger grounding regression

## Reranker Model Sweep Finding

We then benchmarked whether the current reranker model itself should change.

Models compared:

- `cross-encoder/ms-marco-TinyBERT-L2-v2`
- `cross-encoder/ms-marco-MiniLM-L6-v2`
- `cross-encoder/ms-marco-MiniLM-L12-v2`

### Retrieval-Level Result

What was measured:

- page-hit rate
- MRR
- fragment recall
- objective score
- retrieval latency

What we learned:

- `MiniLM-L12-v2` ranked first on retrieval objective
- `MiniLM-L6-v2` ranked second, but with lower latency
- `TinyBERT-L2-v2` was fastest, but not strong enough overall to justify the quality tradeoff

Current interpretation:

- if retrieval ordering alone were the decision surface, `MiniLM-L12-v2` would be the leading candidate

### Internal Answer-Layer Result

What was measured:

- supported rate on positive datasets
- citation page-hit rate
- evidence fragment recall
- abstention rate on the negative set

What we learned:

- `MiniLM-L6-v2` kept the better positive supported-rate profile
- `MiniLM-L6-v2` also preserved the stronger negative-set abstention behavior
- `MiniLM-L12-v2` improved fragment recall slightly, but at the cost of more false support

Current interpretation:

- the answer-layer comparison favors staying with `MiniLM-L6-v2`

### Focused External `ragas` Result

What was measured:

- supported rate
- faithfulness
- answer relevancy
- context precision

What we learned:

- `MiniLM-L6-v2` was better overall on:
  - supported rate
  - faithfulness
  - answer relevancy
- `MiniLM-L12-v2` only edged ahead slightly on overall context precision

Current interpretation:

- the external check reinforces the internal answer-layer result
- `MiniLM-L12-v2` is a stronger retrieval-only model than it is an end-to-end answer model in the current stack
- `cross-encoder/ms-marco-MiniLM-L6-v2` remains the justified production reranker
