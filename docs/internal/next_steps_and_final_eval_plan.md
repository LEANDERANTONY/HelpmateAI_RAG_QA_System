# Internal Next Steps And Final Evaluation Plan

This document is not part of the public README story. It is the working plan for making HelpmateAI easier to maintain and for turning the current benchmark into a more defensible final evaluation.

## Current Position

HelpmateAI has a credible architecture and strong project-benchmark results. The current public claim should stay narrow:

> On the current project workload, HelpmateAI's topology-aware retrieval and abstention pipeline outperforms the tested OpenAI File Search and Vectara retrieval configurations.

We should not claim broad vendor superiority until we run a blind, never-tuned evaluation with tighter controls.

## Workstream 1: Code Quality Polish

These items improve maintainability without changing the product story.

### 1. Extract Retrieval Scoring Weights

Target file:

- `src/retrieval/hybrid.py`

Current issue:

- `_score_chunk` contains many benchmark-tuned constants directly in the scoring logic.
- The behavior may be valid, but the rationale is not readable next to the numbers.

Plan:

- Create a typed `ScoringWeights` dataclass.
- Group weights by purpose:
  - lexical and heading boosts
  - content/clause/scope boosts
  - role and noise penalties
  - summary-route adjustments
  - semantic-role adjustments
- Add short comments for why each group exists.
- Keep defaults behavior-equivalent in the first pass.
- Add focused tests that prove representative chunks keep the same ordering before and after the refactor.

Decision gate:

- Refactor is acceptable only if existing retrieval helper tests pass and a small fixed retrieval fixture keeps identical or near-identical candidate ordering.

### 2. Replace Long Scoring Parameter Lists With Context Objects

Target functions:

- `_score_chunk`
- `_chunk_candidates`

Current issue:

- Long parameter lists make it hard to see which values belong together.

Plan:

- Introduce small context dataclasses, likely:
  - `ScoringContext`
  - `CandidateBuildContext`
- Move query-level state into context:
  - question
  - preferred content types
  - clause terms
  - scoped section IDs
  - region lookup
  - preferred region kinds
  - query type

Decision gate:

- No behavior change in the first pass.
- Keep context classes local to `hybrid.py` unless they become useful elsewhere.

### 3. Split Planner Helpers Once Behavior Is Locked

Target file:

- `src/retrieval/planner.py`

Current issue:

- The planner is behaviorally important but dense.
- Several `_plan_from_*` paths are close enough that future edits could drift.

Plan:

- Do not split this while the final evaluation is still active.
- After the final eval, extract helpers by responsibility:
  - intent detection
  - scope interpretation
  - route selection
  - global-summary planning
  - orchestrator validation
- Preserve the public `RetrievalPlanner` API.

Decision gate:

- Run retrieval planner tests before and after.
- Add snapshot-like tests for known important questions:
  - exact policy lookup
  - thesis local chapter scope
  - broad thesis summary
  - broad paper contribution
  - unsupported/out-of-scope question

### 4. Tidy Loose Top-Level Modules

Candidates:

- `src/query_router.py`
- `src/question_starters.py`

Current issue:

- These are not harmful, but they make the package shape look slightly less intentional.

Plan:

- Move only if there is a clear destination:
  - `src/query_router.py` -> possibly `src/query_analysis/router.py`
  - `src/question_starters.py` -> possibly `src/query_analysis/question_starters.py` or a frontend-facing service module
- Keep compatibility imports for one release if needed.

Decision gate:

- Only do this after heavier evaluation work, because it is cosmetic.

## Workstream 2: Final Evaluation Design

The goal is to test whether the architecture generalizes beyond the tuned project benchmark.

### Evaluation Question

Primary question:

> On unseen long documents and fresh questions, does HelpmateAI produce more grounded, relevant, and precise answers than tested OpenAI File Search and Vectara configurations under equalized evaluation conditions?

Secondary questions:

- Which intent types does HelpmateAI actually win?
- How much of the win comes from abstention?
- How much of the win survives equal context budgets?
- How sensitive are scores to the judge model family?

### Systems To Compare

System A:

- HelpmateAI current default stack.

System B:

- OpenAI File Search.
- Use the best documented configuration available in our harness.
- Current baseline uses `rewrite_query=True`.

System C:

- Vectara.
- Use the best documented configuration available in our harness.

Optional later systems:

- Google Vertex AI Search
- Cohere retrieval/rerank

### Document Set

Use documents that were not used for architecture tuning, threshold tuning, scoring weight sweeps, or prompt debugging.

Minimum final set:

- 1 policy or legal/benefits document
- 1 thesis or dissertation
- 1 research paper in a new domain
- 1 technical report or whitepaper
- 1 messy/noisy PDF with imperfect structure

Stretch set:

- 8-10 documents across the same categories.

Rules:

- Do not inspect HelpmateAI retrieval failures while constructing the question set.
- Store documents under a clearly named held-out location.
- Record source, license/access notes, document type, page count, and whether OCR/layout is clean.

### Recommended Dataset Direction

Use two complementary paths.

Path A: unseen product-fit documents.

Purpose:

- test whether HelpmateAI generalizes to the kinds of long documents the product actually targets
- keep the setup simple enough to run OpenAI File Search, Vectara, and HelpmateAI side by side

Recommended pilot:

- EU AI Act final text
  - structured legal/compliance document
  - good for article/recital lookup, definitions, obligations, exceptions, and summary questions
- NIST AI Risk Management Framework 1.0
  - shorter structured public framework document
  - good for policy/procedure and conceptual questions
- one fresh thesis or dissertation
  - use ProQuest Open Access, MIT DSpace, British Library EThOS, or another public repository
  - choose a field not used in previous tuning
- one fresh arXiv or bioRxiv paper outside current tested domains
  - avoid pancreas/biology overlap unless intentionally testing a near-domain case
  - good for contribution, methods, findings, limitations, and comparison questions
- one messy technical report or public institutional report
  - data-heavy or layout-heavy PDFs are useful because they test robustness

Path B: established benchmark datasets.

Purpose:

- add credibility with human-authored or community-recognized questions
- reduce the criticism that we selected favorable questions
- create a result that is easier to explain to technical reviewers

Recommended anchors:

- QASPER
  - academic-paper QA dataset
  - strong match for research-paper use cases
  - useful for abstractive and evidence-supported questions
- FinanceBench
  - financial filing QA benchmark
  - strong external credibility and human-authored answers
  - less aligned with HelpmateAI's current positioning than QASPER, but valuable as a citable benchmark

Recommended sequence:

1. Run a small pilot on unseen product-fit documents first:
   - EU AI Act
   - NIST AI RMF
   - one new research paper
2. Use the pilot to debug the harness, reporting format, and context-budget controls.
3. Only then run QASPER or FinanceBench.
4. Treat QASPER as the better first established benchmark for the current product story.
5. Treat FinanceBench as a secondary credibility benchmark, especially if we want a finance-document result.

Do not make FinanceBench the only headline unless we want to reposition the product toward financial-document QA.

### Question Set

Generate fresh questions per document.

Minimum:

- 30 questions per document.

Intent distribution target:

| Intent type | Target share |
| --- | ---: |
| exact lookup | 25% |
| local section/chapter scope | 20% |
| broad summary/contribution/conclusion | 20% |
| comparison/synthesis | 15% |
| numeric/procedure/list extraction | 10% |
| unsupported/out-of-scope | 10% |

Generation protocol:

- Use a model or human pass that reads the full document, not HelpmateAI retrieved chunks.
- Avoid writing questions after seeing any system's retrieval results.
- Keep questions natural and not overly tailored to HelpmateAI route names.
- Label each question with:
  - document ID
  - intent type
  - expected evidence region if known
  - whether the question is answerable
  - expected answer notes or gold answer if available

### Gold Answers

Preferred:

- Human-authored or human-reviewed gold answers from full-document reading.

Acceptable first pass:

- Use a different model family from the answer generator to draft gold answers from the full document.
- Human review a subset, especially broad synthesis and unsupported questions.

Gold answer fields:

- concise answer
- supporting page/section references
- answerable flag
- unacceptable answer notes

### Context Budget Fairness

Current issue:

- HelpmateAI uses `final_top_k=4`.
- Vendor harnesses currently use 5 snippets and truncate each snippet to 400 characters.

Final eval should report at least two views:

1. Production-default view:
   - HelpmateAI default final evidence.
   - Vendor tested defaults.

2. Equalized-context view:
   - Same number of contexts per system.
   - Same maximum character budget per context or total context budget.
   - Same shared answer generator.

Recommended equalized setup:

- `k=5` contexts for all systems.
- maximum 400-600 characters per context.
- report total retrieved context characters per system.

### Answer Generation Fairness

Use two answer-generation modes.

Mode 1: shared generator

- Feed each system's retrieval context into the same Helpmate answer generator.
- This isolates retrieval context quality.

Mode 2: native product mode where available

- Let vendor product answer natively if the API supports it.
- Let HelpmateAI answer natively.
- This measures full user-facing product behavior.

Primary comparison should be shared-generator mode. Native mode is secondary because vendor answer APIs may differ too much.

### Judge Model Fairness

Current issue:

- RAGAS uses the configured OpenAI-backed evaluator.

Final eval should run at least:

- OpenAI-backed RAGAS judge, current continuity view.
- One second judge model family if tooling permits.

If second-family RAGAS integration is too expensive or awkward, run a manual/LLM rubric judge on a stratified subset.

### Metrics

Report these for each system overall and per intent type.

Retrieval metrics:

- page/section hit rate
- MRR
- fragment recall where labeled
- context precision
- average context count
- average context character budget

Answer metrics:

- RAGAS faithfulness
- attempted-only RAGAS faithfulness
- RAGAS answer relevancy
- RAGAS context precision
- supported/attempted rate
- abstention rate
- false support rate on unsupported questions
- answer completeness on answerable questions

Operational metrics:

- average latency
- p95 latency
- approximate model/API cost per question
- failure/error rate

### Abstention Reporting

Do not hide abstention inside faithfulness.

Report:

- all-query faithfulness
- attempted-only faithfulness
- abstention rate
- false-abstention rate on answerable questions
- false-support rate on unanswerable questions

Interpretation rule:

- A high-faithfulness system with high abstention is conservative.
- A high-faithfulness system with low false support and strong attempted-only relevancy is genuinely stronger.

### Per-Intent Reporting

Overall averages are not enough.

Break out at least:

- lookup
- local scope
- broad summary
- comparison/synthesis
- numeric/procedure
- unsupported

Expected hypothesis:

- HelpmateAI should lead most strongly on local scope, broad summary, and unsupported questions.
- Vendor systems may be closer on exact lookup.
- Context precision may narrow under equalized context budgets.

### Vendor Tuning Pass

Before final numbers:

- Review current OpenAI File Search API settings.
- Review current Vectara query settings.
- Document every setting used.
- If there are obvious quality knobs, run a small tuning sweep on a development-only document, not the held-out final docs.
- Freeze vendor settings before running final held-out evaluation.

Rule:

- Do not tune vendor settings on the final held-out question set.

### Execution Protocol

1. Freeze HelpmateAI code and settings.
2. Choose held-out documents.
3. Create question set without looking at retrieval outputs.
4. Create or draft gold answers from full-document reading.
5. Freeze vendor settings.
6. Run retrieval for all systems.
7. Run shared-generator answers for all systems.
8. Run RAGAS scoring.
9. Run second judge or rubric subset.
10. Produce overall and per-intent tables.
11. Inspect failures only after metrics are saved.
12. Save full report under `docs/evals/reports/`.
13. Write a short interpretation note in `docs/evals/benchmark_summary.md`.

### Decision Gates For Public Claims

Claim tier 1:

> HelpmateAI outperforms tested vendor configurations on the project benchmark.

Already supported.

Claim tier 2:

> HelpmateAI generalizes to unseen long-document QA better than tested vendor configurations.

Requires:

- held-out documents
- fresh questions
- equalized context budget view
- per-intent breakdown
- abstention-aware metrics

Claim tier 3:

> HelpmateAI beats Vectara/OpenAI File Search broadly.

Do not claim unless:

- multiple held-out domains
- tuned vendor settings
- second judge family or human-reviewed subset
- stable wins on attempted-only metrics, not only all-query faithfulness

## Workstream 3: Future Graph And Workflow Exploration

This workstream should not block the final evaluation. It is a future exploration track for ideas that may improve specific question types or make orchestration easier to inspect.

### LangGraph

LangGraph is a workflow framework, not a retrieval technique. It helps express LLM systems as nodes and conditional edges.

HelpmateAI already has a graph-shaped workflow in imperative Python:

- ingest
- conditional structure repair
- section enrichment
- topology build
- query analysis
- deterministic planner
- optional LLM orchestrator
- optional planner route refinement
- route-specific retrieval
- fusion and reranking
- evidence grading
- conditional evidence selection
- abstention or grounded answer generation
- run tracing

Potential value:

- visual workflow representation
- checkpointing and replay
- clearer node-level traces
- easier experimentation with new branches
- useful literacy for interviews and future agent projects

Current decision:

- Do not retrofit HelpmateAI to LangGraph now.
- Learn it separately and consider it for a future project or a small isolated prototype.
- Keep HelpmateAI's current typed, deterministic pipeline unless there is a concrete debugging or product need that LangGraph solves better.

### GraphRAG Versus HelpmateAI's Topology Layer

GraphRAG and HelpmateAI's topology layer are related but not the same.

Current HelpmateAI graph:

- nodes are document sections or regions
- edges describe structure and local semantic proximity:
  - parent/child
  - previous/next
  - same region family
  - semantic neighbor
- best for long-document navigation, scoped retrieval, and broad section-aware questions

Typical GraphRAG graph:

- nodes are entities, concepts, events, methods, datasets, drugs, authors, organizations, or other extracted knowledge units
- edges describe relationships between those units
- best for entity-centric and multi-hop questions, especially across larger corpora

Current decision:

- Do not build full GraphRAG before the final evaluation.
- Full entity/relation extraction would add index-time cost and a large new evaluation surface.
- For the current single-document product, expected gains are narrow.

### Candidate Sixth Route: `entity_first`

The useful slice of GraphRAG for HelpmateAI may be a lightweight entity-guided retrieval route rather than a full knowledge graph.

Hypothesis:

> Entity-aware section retrieval may improve entity-centric and multi-hop questions without disrupting the existing document-topology architecture.

Possible implementation:

- extract named entities and key concepts per section during indexing
- store `entity_terms` on section or synopsis metadata
- build a lightweight inverted index:
  - entity/concept term -> section IDs
- add planner detection for entity-heavy questions
- add a sixth route:
  - `entity_first`
- route flow:
  - detect entity terms from question
  - match entity terms to section IDs
  - retrieve chunks from matched sections
  - merge with chunk-first or synopsis-first candidates
  - rerank and grade evidence as usual
- keep answer grounding limited to raw chunks, not entity summaries

Likely wins:

- "Where does this document discuss X?"
- "How is X connected to Y?"
- "Compare how the document treats A and B."
- "What does the paper say about this dataset/model/drug/concept?"
- cross-section questions where entity names are more reliable anchors than section titles

Likely low value:

- exact policy clause lookup
- broad conclusion or contribution questions already handled by `global_summary_first`
- local chapter questions already handled by orchestrated scope
- generic summary questions

Evaluation plan for `entity_first`:

- add it only after the final held-out benchmark is frozen
- run it as an ablation, not as an assumed improvement
- compare against current default on entity-centric questions only
- track:
  - page/section hit rate
  - MRR
  - context precision
  - answer relevancy
  - latency and index-time cost
- promote only if it improves entity-centric questions without regressing the main benchmark

Public framing if it works:

> HelpmateAI uses planned hybrid retrieval over a document topology. The entity-guided route borrows a narrow GraphRAG idea for entity-heavy questions without turning the system into a full knowledge-graph pipeline.

## Immediate Next Actions

1. Let the current evaluation run finish.
2. Save the report path and exact settings.
3. Compare the result against the caveats in this document.
4. Do not retune on the final held-out set.
5. Start the scoring-weight refactor only after current eval artifacts are saved.
6. Create the blind eval document list and question-generation protocol.

## Notes To Future Us

The goal is not to make the benchmark smaller or less confident. The goal is to make it harder to dismiss.

Honest caveats are not weakness. They are what make the strong parts believable.
