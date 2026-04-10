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
- the next major product step is clearer: a stronger custom frontend on top of the current Python core

## 16. FastAPI And Next.js Transition

Change:

- started moving the product shell to `FastAPI + Next.js` while keeping Streamlit for internal benchmarking

Challenge:

- the retrieval core had become more mature than the existing frontend presentation
- we needed a cleaner user-facing shell without rewriting the Python retrieval system again

Improvement:

- the project now has a clearer production-facing direction
- the transport boundary is cleaner and more reusable than before

## 17. Retrieval Simplification And Guardrails

Change:

- removed model-based query rewriting
- moved to deterministic weak-evidence recovery plus explicit unsupported guardrails

Challenge:

- rewrite variability was helping some questions while hurting others
- obviously irrelevant questions needed to fail early, not after a full answer-generation pass

Improvement:

- retrieval behavior became more predictable
- unsupported questions now short-circuit cleanly through retrieval guardrails

## 18. Document-Topology Retrieval

Change:

- added deterministic retrieval planning
- added section synopses and topology edges
- added synopsis-first hierarchical retrieval with soft multi-region guidance and global fallback

Challenge:

- structure existed in the system, but was still too passive
- broad narrative questions needed section-aware control without losing the multi-page retrieval behavior that already worked well

Improvement:

- structure is now an active retrieval control signal
- thesis, policy, and research-paper workflows now share a more general retrieval shape based on question type and evidence spread

## 19. Bounded Post-Rerank Evidence Selection

Change:

- added a post-rerank evidence selector before answer generation

Challenge:

- some failures were not true retrieval failures
- the right chunk was already present in top `k`, but not at rank 1

Improvement:

- the system can now prefer a lower-ranked but more direct chunk without rerunning retrieval
- this keeps the intervention bounded and inspectable
- unsupported-question guardrails still apply before the selector can run
- `pancreas8` improved materially
- thesis future-work style questions became better supported

## 20. Benchmark Refresh After Retrieval Simplification

Change:

- reran the full four-document benchmark suite after the section-retrieval and guardrail changes

Challenge:

- the suite is slow because each document combines multiple eval families and vendor comparisons
- long wrapper processes can exceed local tool windows even when underlying reports complete

Improvement:

- confirmed no meaningful regression from removing model-based query rewriting
- confirmed the strongest win on `pancreas8`
- clarified that thesis and `pancreas7` are now the most justified retrieval-quality targets

## 21. Low-Confidence Structure Repair During Indexing

Change:

- added an indexing-time structure-repair layer for low-confidence journal-style PDFs
- kept deterministic parsing first
- only used a small model when structural confidence looked suspicious

Challenge:

- some publisher-formatted papers flattened section boundaries badly enough that topology and synopsis retrieval were being built on weak structure
- doing more LLM work in the live query path would have increased latency and instability

Improvement:

- messy PDFs can now get a cleaner section map before topology is built
- the repair cost is paid once at indexing time, not on every question
- the old benchmark docs did not regress after adding this layer

## 22. Dedicated Global-Summary Evidence Route

Change:

- added a dedicated `global_summary_first` evidence route for broad paper-summary questions
- improved prompt handling for global-summary answers
- added report-generation retrieval and negative eval datasets

Challenge:

- some broad paper questions were still failing even when retrieval already surfaced relevant evidence
- the system needed a better overview/findings/conclusion evidence bundle rather than another retrieval rewrite

Improvement:

- `reportgeneration` broad summary behavior improved meaningfully
- `reportgeneration2` main-contribution behavior recovered
- the four benchmark documents stayed stable or slightly better while the dedicated summary route was added

## What This Means For The Next Step

The architecture is now strong enough that the next improvement should not be another repo restructure.

The most justified next steps are now:

- build a stronger custom frontend on top of the existing core
- keep the current retrieval architecture stable unless the remaining broad-summary edge cases justify a small targeted pass
- add harder benchmark sets spanning multiple document families
- expose the backend more cleanly if the new frontend later needs an API boundary
