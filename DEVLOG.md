# DEVLOG - HelpmateAI

This document tracks the major implementation changes, the problems we hit, and how we improved the system.

Historical note:

- earlier entries reflect the first app baseline
- later entries add quality-control, benchmarking, and document-intelligence work on top of that baseline
- the project is still evolving, so later entries refine earlier architectural assumptions without erasing them

## Day 1: Notebook-To-App Restructure

- Refactored the repository from a notebook-first layout into a real app structure.
- Added:
  - `app.py`
  - `src/`
  - `tests/`
  - `docs/`
  - Docker and Render scaffolding
- Standardized dependency management on `pyproject.toml` and `uv.lock`.
- Kept the original notebook as a reference artifact rather than the main implementation surface.

Challenges:

- the original notebook mixed ingestion, retrieval, generation, and experimentation in one place
- the repo looked like a demo rather than a deployable product
- dependency management was not aligned with sibling projects

Improvements:

- moved core logic into reusable modules
- made Streamlit a thin UI shell instead of the business-logic home
- aligned the repo with the same project shape used successfully in sibling apps

## Day 2: Local-First RAG Baseline

- Implemented PDF and DOCX ingestion.
- Added deterministic chunking and Chroma-backed persistent indexes.
- Added hybrid retrieval:
  - dense retrieval
  - TF-IDF lexical retrieval
  - fusion
  - reranking hook
- Added answer caching and citation-aware answer generation.

Challenges:

- the first pass was structurally sound but not yet quality-controlled
- long-document retrieval quality was hard to judge from manual spot checks alone
- policy-style questions needed exact-term retrieval support, not only embeddings

Improvements:

- hybrid retrieval gave the system better exact-term behavior
- persisted indexes made repeated testing practical
- typed pipeline boundaries made later tuning easier

## Day 3: Product Shell, Deployment, and Tests

- Added the Streamlit UI with the same visual language as the AI Job Application Agent.
- Added:
  - `.streamlit/config.toml`
  - Dockerfile
  - Render manifest
  - CI
  - initial focused tests

Challenges:

- the repo needed to become presentation-ready, not just technically runnable
- deployment and local dev setup needed to feel consistent with the other portfolio projects

Improvements:

- created a clean app shell that can be demoed and deployed
- verified core helpers with tests instead of relying only on manual runs

## Day 4: Retrieval Quality Controls

- Added:
  - retrieval eval dataset
  - negative abstention eval dataset
  - OpenAI file-search benchmark harness
  - saved benchmark reports
- Started measuring local RAG against a hosted retrieval baseline.

Challenges:

- early retrieval quality looked weaker than expected
- some supposed retrieval failures were actually problems in the eval labels
- without saved benchmark reports, improvements were too easy to overclaim

Improvements:

- benchmark labels were corrected to match the real document contents
- comparison against OpenAI gave us a meaningful external baseline
- benchmark reports became part of repo history under `docs/evals/reports/`

## Day 5: Query Rewriting, Metadata-Aware Retrieval, and Adaptive Retry

- Added query rewriting fallback.
- Added page-aware retrieval filters and heading-aware ranking.
- Added adaptive re-retrieval when evidence is weak.
- Improved citation rendering and retrieval transparency in the UI.

Challenges:

- policy documents often mix exact clauses, definitions, and operational sections
- weak evidence needed to trigger retrieval recovery rather than immediately flowing into answer generation
- retrieval needed more transparency to debug what was actually happening

Improvements:

- retrieval became more inspectable
- page-specific and heading-specific questions became more reliable
- the system could now retry with better query variants when the first pass was weak

## Day 6: Structured Abstention

- Moved answer generation to a stricter structured output contract.
- Added explicit `supported` status to answer results.
- Updated the UI to show supported versus unsupported answers.
- Updated the negative eval to judge abstention using the typed output rather than fuzzy wording.

Challenges:

- unsupported questions could still produce vague answers
- string-based abstention checks were brittle

Improvements:

- unsupported questions now fail more honestly
- abstention became measurable and testable

## Day 7: Health-Policy Benchmark Generalization Pass

- Added a new benchmark set for the health insurance wording document.
- Compared local retrieval and OpenAI hosted retrieval on a second policy document family.

Challenges:

- Chroma emitted repeated telemetry warnings that looked like hangs even when runs completed
- the health-policy PDF was encrypted and needed an extra dependency for reliable parsing
- some retrieval misses were true clause-level misses rather than eval mistakes

Improvements:

- added `cryptography` to support encrypted PDF handling
- confirmed the architecture generalized beyond the first policy sample
- identified that the remaining weakness was not the app shell, but clause-level retrieval precision

## Day 8: Base Snapshot And Safe Rollback Point

- Committed and pushed the benchmarked baseline to `main` before further architectural changes.

Challenges:

- we needed a safe fallback before introducing a smarter retrieval layer
- local config and ad hoc benchmark files needed to stay out of git

Improvements:

- updated `.gitignore`
- created a clean rollback point before changing retrieval architecture further

## Day 9: Document-Intelligence Layer

- Added:
  - `src/structure/`
  - `src/query_analysis/`
- Enriched ingestion with:
  - section headings
  - clause ids
  - section paths
  - content types
- Upgraded chunking to preserve semantic structure metadata.
- Updated retrieval to use query classification and soft structural preferences during ranking.

Challenges:

- repeated document-specific tuning risked overfitting to one PDF family
- policy-style improvements did not necessarily transfer to thesis-style or narrative documents
- richer metadata introduced storage constraints in Chroma

Improvements:

- moved from ad hoc document-specific boosts toward structure-aware retrieval
- added a more portable middle layer between raw text and answer generation
- positioned the system for future hierarchical retrieval

## Day 10: Thesis Benchmark And Portability Learnings

- Added thesis-specific positive and negative benchmark datasets.
- Benchmarked the upgraded pipeline on a very different document style: a long academic thesis.
- Fixed Chroma metadata sanitization for list-valued fields produced by the new structure layer.

Challenges:

- Windows terminal output failed on some Unicode dissertation symbols during inspection
- Chroma rejected list-valued metadata such as `section_path`
- retrieval quality dropped on thesis-style narrative questions compared with policy documents

Improvements:

- sanitized Chroma metadata at the storage boundary while preserving structured metadata locally
- confirmed the system still generalizes beyond policy documents
- learned that the next major weakness is broader narrative and section-level synthesis, not just exact-clause retrieval

## Current Summary

Current system strengths:

- strong modular app architecture
- local-first inspectable RAG stack
- explicit abstention and benchmark discipline
- measurable outperformance versus hosted OpenAI retrieval on the current document-specific benchmarks
- first document-intelligence layer now in place

Current weaknesses:

- retrieval is much stronger on structured policy documents than on long academic prose
- section-level and cross-section narrative questions remain harder than factual clause lookups
- Chroma telemetry remains noisy in terminal output even though it does not block runs

## Day 11: Section-First Retrieval Layer

- Added section records and persisted section indexes.
- Added dual retrieval paths:
  - `chunk_first`
  - `section_first`
  - `hybrid_both`
- Added a lightweight query router to choose between those retrieval paths.
- Added focused tests for section building and query routing.

Challenges:

- we needed broader-question support without sacrificing the already strong factual benchmark
- replacing chunk retrieval entirely would have weakened clause-heavy policy questions
- older indexes on disk did not contain sections and needed safe rebuild behavior

Improvements:

- kept exact chunk-grounded retrieval as the primary factual path
- added section-first narrowing for synthesis-heavy questions
- made routing behavior visible in retrieval notes and benchmark outputs

## Day 12: Better Section Summaries And Academic-Document Handling

- Improved section construction for theses and research papers.
- Added:
  - canonical heading detection
  - cleaner section titles
  - better section summaries
  - suppression of common author-manuscript and reference-style noise
- Added section-kind aware ranking preferences such as:
  - `Abstract`
  - `Introduction`
  - `Results`
  - `Conclusion`
  - `Future Work`

Challenges:

- narrative documents do not behave like policy wording documents
- broad questions such as “main aim” or “future work” can be misclassified as factual lookups
- review papers often contain front matter and bibliography text that pollute section retrieval

Improvements:

- thesis benchmark improved from `0.75 / 0.5486` to `0.8333 / 0.5903`
- the pancreas8 review-paper benchmark improved from `0.8 / 0.75` to `0.9 / 0.85` at the best section-summary stage
- policy benchmark stability was preserved while narrative retrieval improved

## Day 13: Lightweight LLM Router Trial And Latency Check

- Added a lightweight LLM-assisted router as a tie-breaker for low-confidence mixed queries.
- Added timing instrumentation to measure router overhead in the live pipeline.

Challenges:

- heuristic routing still struggled on some broad paper-style questions
- it was unclear whether an LLM router would meaningfully help or just add latency

Improvements and learnings:

- the LLM router remained lightweight and bounded; it only selects a retrieval route
- it is not a full agent and does not answer questions itself
- latency impact is limited because it only runs on low-confidence cases
- on one broad paper-style query, the router added about `1.37s` but reduced total runtime by avoiding a heavier `hybrid_both` path
- on clean factual questions, the router is usually not the bottleneck

Current caution:

- the LLM router is useful as a tie-breaker, but it is not yet a guaranteed accuracy gain across every benchmark
- document parsing quality is still the stronger next lever than adding more routing complexity

## Day 14: Layered Evaluation With Ragas

- Added `ragas` as an open-source evaluation layer on top of the existing benchmark harness.
- Added:
  - `src/evals/ragas_eval.py`
  - `tests/test_ragas_eval.py`
  - `docs/evals/README.md`
- Updated the benchmark comparison runner so saved reports now include:
  - custom retrieval metrics
  - negative abstention metrics
  - OpenAI hosted retrieval baseline
  - `ragas` answer faithfulness
  - `ragas` answer relevancy
  - `ragas` no-reference context precision

Challenges:

- the new `ragas` version in this environment did not work cleanly with the newer factory pattern, even though that is the direction recommended in the docs
- the practical working bridge used LangChain wrappers, which are deprecated upstream but stable enough for the current repo
- our existing eval datasets are retrieval-labeled, not gold-answer datasets, so we had to start with no-reference answer-quality metrics rather than full reference-based scoring
- full comparison runs are now slower because they include extra LLM-evaluator passes

Improvements:

- we can now distinguish retrieval errors from answer-quality errors more clearly
- broad academic-paper questions are easier to diagnose because `ragas` exposes cases where retrieval is acceptable but answer relevance is weak
- benchmark reports are closer to a real evaluation matrix instead of a single score family

Latest concrete example:

- on `static/pancreas8.pdf`, the first combined report showed:
  - local retrieval: `0.8 / 0.8`
  - negative abstention: `1.0`
  - OpenAI file search: `0.4`
  - `ragas` faithfulness: `0.8429`
  - `ragas` answer relevancy: `0.4885`
  - `ragas` context precision: `0.8056`

Key takeaway:

- retrieval and grounding remain decent on the review paper
- the weaker area is answer relevance for broad paper-summary questions
- that gives us a much sharper picture of what to improve next

## Day 15: Vectara Benchmarking And Eval Policy Simplification

- Added Vectara as an external retrieval benchmark.
- Added a shared-answer comparison path so OpenAI and Vectara retrieval contexts could be judged under the same answer model.
- Captured answer-quality comparison across:
  - Helpmate
  - Vectara retrieval plus shared answer model
  - OpenAI retrieval plus shared answer model
- Added benchmark summary documentation under:
  - `docs/evals/benchmark_summary.md`

Challenges:

- Vectara factual-consistency scores were highly sensitive to our answer formatting and did not align well enough with the rest of the benchmark picture
- full vendor answer-eval runs are slow because they combine retrieval, answer generation, and multiple evaluator passes
- OpenAI File Search consistently lagged as an external baseline on the tested document families

Improvements and decisions:

- Vectara is now the primary external retrieval benchmark
- OpenAI File Search remains in the repo as a historical/reference retrieval baseline
- `ragas` is now the only answer-quality meter we use in routine benchmarking
- vendor factual-consistency APIs are no longer part of the active benchmark decision loop
