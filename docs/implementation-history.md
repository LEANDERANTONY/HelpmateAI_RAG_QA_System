# Implementation History And Challenges

This document summarizes the important engineering changes made so far, the problems they exposed, and the improvement each change unlocked.

## 1. From Notebook Demo To App Architecture

Change:

- moved from a notebook-driven prototype to a Streamlit app with modular `src/` services

Challenge:

- the notebook mixed experimentation, ingestion, retrieval, and answer generation in one place
- there was no clear deployment path and no clean surface for testing

Improvement:

- separated UI, pipeline, retrieval, generation, and caching concerns
- made the project deployable and easier to evolve safely

## 2. Local-First Persistent RAG

Change:

- added Chroma-based persistence, reusable indexes, and typed chunk records

Challenge:

- repeated testing was slow without index reuse
- retrieval behavior was hard to inspect when everything lived in notebook cells

Improvement:

- persisted indexes made experimentation practical
- saved chunk metadata made retrieval debugging easier

## 3. Hybrid Retrieval Instead Of Embeddings-Only

Change:

- combined dense retrieval with TF-IDF lexical retrieval and fusion

Challenge:

- policy documents contain exact clauses, definitions, and phrasing that pure vector retrieval can miss

Improvement:

- better exact-term recall
- better handling of clause-like questions and domain-specific wording

## 4. Reranking, Query Rewriting, And Adaptive Retry

Change:

- added reranking, query rewriting fallback, and weak-evidence re-retrieval

Challenge:

- first-pass retrieval was not always good enough for grounded answers
- users need strong retrieval before answer generation matters

Improvement:

- more resilient retrieval pipeline
- clearer strategy notes and retrieval transparency

## 5. Structured Abstention

Change:

- introduced explicit `supported` output in answer generation

Challenge:

- unsupported questions could still produce vague or overconfident text

Improvement:

- unsupported answers now fail more honestly
- negative evals became more meaningful

## 6. Benchmark-Driven Quality Control

Change:

- added positive evals, negative evals, external retrieval baselines, and saved reports

Challenge:

- without evals, it was too easy to mistake anecdotal success for real retrieval quality
- some early misses turned out to be bad benchmark labels rather than retrieval failures

Improvement:

- quality claims became measurable
- the team could compare local RAG against external baselines on the same documents

## 7. Metadata-Aware Retrieval

Change:

- added page-aware filtering, section-heading signals, and richer citations

Challenge:

- many queries refer to pages, sections, or exact clause neighborhoods

Improvement:

- retrieval became more controllable and easier to interpret

## 8. Document-Intelligence Layer

Change:

- added structure inference and query analysis before ranking

Challenge:

- document-specific tuning risked overfitting
- improvements that helped one policy PDF would not necessarily generalize to a thesis or report

Improvement:

- chunk metadata now carries section paths, clause ids, and content types
- retrieval can softly prefer definition, waiting-period, process, or benefit-style evidence depending on the question

## 9. Chroma Portability Issues

Change:

- sanitized metadata written to Chroma while preserving rich local chunk metadata

Challenge:

- Chroma only accepts scalar metadata values
- the new structure-aware layer produced lists such as `section_path` and `clause_ids`

Improvement:

- structured metadata now works with the local store and Chroma index persistence
- the richer retrieval layer remains compatible with the storage backend

## 10. Cross-Document Generalization Findings

What worked well:

- policy wording documents
- exact factual questions
- clause-adjacent operational queries
- negative abstention behavior

What became harder:

- thesis and research-style narrative content
- synthesis across broader sections
- future-work and interpretive questions
- result-summary queries that are semantically distributed rather than concentrated in one clause block

## 11. Dual Retrieval Paths

Change:

- added persisted section records and a second retrieval path built around section-first narrowing

Challenge:

- broad questions needed better document navigation, but chunk-first retrieval was already strong for factual and clause-heavy questions

Improvement:

- the architecture now supports:
  - `chunk_first`
  - `section_first`
  - `hybrid_both`
- broad and mixed questions can use section context without giving up raw chunk grounding for final answers

## 12. Academic-Document Parsing And Section Summaries

Change:

- improved section construction with canonical heading detection, cleaner titles, and more useful section summaries

Challenge:

- theses and review papers contain front matter, bibliography clutter, and broader narrative sections that do not behave like policy clauses

Improvement:

- thesis and review-paper retrieval improved without degrading policy retrieval
- section-aware ranking became more document-aware rather than simply page-aware

## 13. Lightweight LLM Router

Change:

- added a small LLM-assisted tie-breaker router for low-confidence mixed queries

Challenge:

- heuristic routing alone still misclassified some broad narrative questions
- a full agent system would have added unnecessary complexity

Improvement:

- ambiguous questions can now use a bounded LLM route decision
- the system remains a deterministic staged pipeline overall, not a multi-agent architecture

## 14. Benchmark Policy Simplification

Change:

- promoted Vectara to the main external retrieval baseline
- kept OpenAI File Search as a historical/reference baseline
- standardized routine answer-quality evaluation on `ragas`

Challenge:

- too many partially overlapping eval signals can make product decisions noisier instead of clearer
- vendor factual-consistency APIs turned out to be very sensitive to answer formatting

Improvement:

- the evaluation story is now easier to interpret
- retrieval benchmarking and answer-quality benchmarking have clearer roles

## 15. Product-Surface Upgrade In Streamlit

Change:

- added document status panels, benchmark snapshots, and style-aware starter questions to the app

Challenge:

- the current backend quality was outgrowing the credibility of the frontend shell

Improvement:

- the app now reflects more of the real system maturity

## 16. Benchmark-Driven Stack Validation

Change:

- added an explicit architecture-eval layer for the retrieval and answer stack
- calibrated planner/router thresholds before judging planner usefulness
- added answer-stack, latency/cost, and focused `ragas` variant comparisons
- recorded the resulting architecture position in dedicated eval docs and an ADR

Challenge:

- retrieval, reranking, routing, and evidence selection all looked reasonable in isolation
- but the project needed to know which layers were actually earning their complexity and runtime cost
- some components improved narrower metrics while harming the more important grounded-answer behavior

Improvement:

- reranker is now clearly justified instead of merely assumed useful
- planner/router now has calibrated thresholds and a measured, modest quality gain
- selector was initially documented as experimental because the first benchmarked implementation regressed

## 17. Chunking And Reranker Defaults Were Finally Benchmarked, Not Guessed

Change:

- added dedicated chunking sweeps, answer-layer comparisons, and focused `ragas` checks
- added reranker-model comparisons rather than assuming the first strong cross-encoder was the right default
- promoted the chunking default from `1200 / 180` to `1200 / 240`
- kept `cross-encoder/ms-marco-MiniLM-L-6-v2` as the reranker after model comparison

Challenge:

- several important inner-loop defaults had grown from sensible heuristics rather than direct measurement
- the first chunking sweep also exposed an index-reuse bug, so the earlier readout could not be trusted

Improvement:

- chunking now reflects a measured quality tradeoff rather than an inherited default
- the reranker model choice is now benchmarked instead of arbitrary
- the evaluation story became strong enough to defend in documentation and interviews

## 18. The Selector Regression Turned Out To Be A Pruning Bug, Not A Reordering Failure

Change:

- traced the selector path and isolated prune mode from reorder-only mode
- added matched retrieval, answer-layer, and focused `ragas` comparisons for:
  - selector off
  - selector prune
  - selector reorder-only
- promoted reorder-only selection back into the default stack
- later calibrated the selector trigger policy to spread-only activation

Challenge:

- the original benchmark conclusion was valid for the code at the time, but it conflated two effects:
  - evidence reordering
  - loss of supporting context

Improvement:

- the project now has a much cleaner evidence-selection design
- selector value is benchmark-justified again
- architecture governance improved because later experiments were designed to overturn or confirm earlier findings honestly

## 19. Product Deployment, Auth, Retention, And Cleanup Caught Up To The Backend

Change:

- completed the `Next.js + FastAPI` product surface
- deployed the frontend on Vercel and the backend on a VPS behind Caddy
- added user-scoped workspaces with Google/Supabase auth
- added resumable `24h` sliding retention
- added VPS-side cleanup for stale uploads, indexes, and cached answers
- added direct-to-API upload handling for larger files

Challenge:

- the product shell had lagged behind the maturity of the retrieval core
- browser uploads through the Vercel proxy hit request-size limits
- retention needed to clear both database rows and local disk artifacts even when users never returned

Improvement:

- the deployed product now matches the architecture we benchmark
- large uploads work through the direct API path
- workspace retention is now a real lifecycle, not just a UI concept

## 20. Final Internal Sweeps Closed The Remaining Major Default Questions

Change:

- completed selector trigger sweeps, synopsis/topology retrieval-default sweeps, structure-repair threshold sweeps, and topology edge ablations
- promoted:
  - `global_fallback_top_k = 3`
  - `planner_candidate_region_limit = 10`
- kept:
  - `synopsis_section_window = 4`
  - synopsis top-k pool `8 / 8 / 5`
  - `structure_repair_confidence_threshold = 0.62`
  - current topology edge sets

Challenge:

- several retrieval defaults were still reasonable but not fully benchmark-backed
- some components showed wide performance plateaus, which makes decision-making harder rather than easier

Improvement:

- the default stack is now mostly measured end to end
- later retrieval changes can be judged against a much stronger baseline

## 21. Final External Vendor Rerun On The Stabilized Stack

Change:

- reran the OpenAI and Vectara comparisons against the stabilized local stack
- fixed the local `ragas` harness so selector behavior is reflected in evaluation just as it is in the live app
- refreshed the published benchmark summary with the new external numbers

Challenge:

- vendor comparisons are expensive enough that they should be rerun only after the internal stack stabilizes
- the local answer-quality harness had to match the selector-enabled production path to make the comparison credible

Improvement:

- Helpmate now has a current external benchmark snapshot on the stabilized architecture
- the project can now say, with a fresher evidence trail, that it leads both tested external baselines on the four main document families
- the repo now has a much cleaner architecture record than it did during the earlier streamlit-first phase

## Current Position

The architecture is now in a different phase than the one this document began with.

The main transitions are complete:

- the product surface is deployed on `Next.js + FastAPI`
- the retrieval stack defaults are benchmark-backed rather than mostly intuition-backed
- the selector story is resolved in favor of reorder-only selection
- the external OpenAI and Vectara comparisons have been rerun on the stabilized stack

So the next justified work is no longer "make the stack real." It is:

- broaden the eval corpus
- add answer-quality coverage for the newer report-generation datasets
- add gold-answer coverage for a selected benchmark subset
- revisit external vendor comparisons only after materially new architecture changes
