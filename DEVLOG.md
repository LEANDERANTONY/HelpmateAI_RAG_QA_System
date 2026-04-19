# DEVLOG - HelpmateAI

This document tracks the major implementation changes, the problems we hit, and how we improved the system.

Historical note:

- earlier entries reflect the first app baseline
- later entries add quality-control, benchmarking, and document-intelligence work on top of that baseline
- the project is still evolving, so later entries refine earlier architectural assumptions without erasing them

## Day 24: Final Vendor Rerun On The Stabilized Stack

- Reran the external `ragas` benchmark suite against both:
  - OpenAI File Search
  - Vectara retrieval
- Fixed the local `ragas` harness so selector-enabled generation is evaluated the same way the live pipeline behaves.
- Updated the published benchmark summary to the new `2026-04-19` stabilized snapshot.

Challenges:

- the external comparisons are expensive enough that they only make sense after the internal defaults settle
- the local answer-quality harness had quietly drifted from the live selector-enabled path and needed correction before the final rerun was trustworthy

Improvements:

- the repo now has a fresh external benchmark snapshot on the current shipping architecture
- Helpmate now leads Vectara by `+0.1997 / +0.1350 / +0.1523` and OpenAI File Search by `+0.4532 / +0.4021 / +0.3697` on average for faithfulness / answer relevancy / context precision across the four main document families

## Day 23: Deployment, Auth, Retention, And Cleanup Finally Matched The Retrieval Core

- Finished the `Next.js + FastAPI` product path.
- Deployed:
  - `app.helpmateai.xyz` on Vercel
  - `api.helpmateai.xyz` on a VPS behind Caddy
- Added Google/Supabase sign-in.
- Added one-active-document-per-user with resumable `24h` sliding retention.
- Added VPS-side cleanup for:
  - uploads
  - local indexes
  - stale answer-cache files
- Switched larger browser uploads to the direct API path to avoid Vercel request-size limits.

Challenges:

- the product shell had fallen behind the maturity of the retrieval stack
- retention needed to clean both database state and local disk artifacts, even when users never returned
- browser uploads through the Vercel proxy broke on larger files

Improvements:

- the live product now behaves like the architecture we benchmark
- large uploads no longer depend on Vercel's body-size ceiling
- workspace resume and expiry are now real product behavior, not just a backend concept

## Day 22: Selector Calibration Closed The Loop On The Earlier Architecture Doubt

- Completed reorder-only selector follow-up sweeps for:
  - weight blend
  - gap threshold
  - trigger-source policy
- Promoted the selector back into the default stack in reorder-only mode.
- Set production selector policy to:
  - `spread-only`
  - no ambiguity trigger
  - no weak-evidence-only trigger

Challenges:

- the selector had already been disabled once, so re-enabling it needed stronger evidence than a single win
- always-on selection improved grounding, but we needed a cleaner production tradeoff than "best metric at any cost"

Improvements:

- reorder-only selector is now a benchmarked default rather than an unresolved experiment
- the selector only activates where it helps most, instead of behaving like a universal second-stage answer tax

## Day 21: Chunking, Reranker Model Choice, And Default Retrieval Settings Became Measured Choices

- Ran the first real chunking sweep and answer-layer follow-up.
- Promoted chunk overlap from `180` to `240`.
- Compared reranker models and kept `cross-encoder/ms-marco-MiniLM-L-6-v2`.
- Completed retrieval-default sweeps for:
  - synopsis section window
  - synopsis top-k pool
  - global fallback pool
  - planner candidate region limit
- Completed repair-threshold and topology-edge sweeps.

Challenges:

- several important defaults had grown from "reasonable" intuition rather than direct measurement
- the first chunking experiment exposed an index-reuse bug, so the evaluation harness itself had to be corrected before the result could be trusted

Improvements:

- the stack defaults now reflect measured tradeoffs instead of inherited settings
- the project gained a much stronger documentation and interview story around benchmark discipline

## Day 14: Architecture Ablations, Threshold Calibration, And Stack Scorecard

- Added a layered architecture-eval workflow rather than relying on a single benchmark.
- Added and saved:
  - selector weight sweep
  - selector on/off ablation
  - reranker on/off ablation
  - planner/router threshold sweep
  - planner ablation
  - answer-stack ablation
  - latency/cost benchmark
  - focused `ragas` stack comparison
- Added a compact architecture scorecard and roadmap under `docs/evals/`.
- Recorded the benchmark-driven architecture decision in a new ADR.

Challenges:

- some of the most plausible layers were the hardest to justify cleanly because they improved one signal while hurting another
- planner usefulness could not be judged fairly until its heuristic confidence thresholds were calibrated
- selector tuning risked looking persuasive on its own even though the real question was whether the selector should remain in the default stack at all

Improvements:

- reranker is now strongly justified with both retrieval-level and answer-level evidence
- planner/router is now calibrated and documented as a modest positive rather than a dramatic win
- evidence selector is now documented as experimental instead of implicitly assumed to be part of the best default architecture
- Helpmate now has a proper evidence trail for why each major layer stays or remains under review

## Day 15: Selector Pruning Bug Isolated And Overturned

- Revisited the selector conclusion from the earlier stack ablations.
- Traced the selector path and confirmed the old implementation was not just reranking evidence:
  - it was pruning the final answer context down to a bounded shortlist
- Added a reorder-only selector mode controlled by:
  - `HELPMATE_EVIDENCE_SELECTOR_ENABLED`
  - `HELPMATE_EVIDENCE_SELECTOR_PRUNE`
- Added matched comparison harnesses for:
  - retrieval-only selector-off vs prune vs reorder-only
  - answer-layer selector-off vs prune vs reorder-only
  - focused `ragas` selector-off vs prune vs reorder-only
- Recorded the architecture update in a new ADR.

Challenges:

- the earlier selector verdict was correct for the code we had, but it was not isolating the true variable
- the first selector-specific `ragas` rerun also had a harness issue and had to be discarded before we could trust the final result
- the selector gate turned out to fire on most benchmark questions, so any production recommendation had to accept a real latency tradeoff rather than a tiny edge-case cost

Improvements:

- proved the regression came from pruning, not from evidence reordering itself
- reorder-only selector outperformed both selector-off and prune mode on the matched retrieval and answer-layer comparisons
- focused `ragas` also flipped in favor of reorder-only, with stronger faithfulness and context precision than the planner+rereanker baseline
- selector is now promoted back into the default stack, but only in reorder-only mode

## Day 11: Document-Topology Retrieval Upgrade

- Added a deterministic `RetrievalPlan` layer ahead of retrieval.
- Added lightweight topology artifacts:
  - section synopses
  - topology edges
  - generic region kinds
- Added a dedicated synopsis collection alongside chunk and section collections in local Chroma storage.
- Reworked retrieval to support:
  - `chunk_first`
  - `synopsis_first`
  - soft local structural guidance
  - soft multi-region retrieval with global fallback
  - hard-region behavior only for explicit page, clause, or named-section references
- Removed the active query rewriting layer from retrieval.
- Added structure-aware retrieval metrics:
  - `section_hit_rate`
  - `region_hit_rate`
  - `plan_accuracy`
  - `global_fallback_recovery_rate`
  - `multi_region_recall`

Challenges:

- planner mistakes can bias retrieval more than plain similarity-only search
- synopsis quality had to stay factual and lightweight rather than lossy
- distributed questions needed structural guidance without collapsing the current multi-page evidence behavior

Improvements:

- health-policy retrieval stayed stable through the upgrade
- `pancreas7` and `pancreas8` remained strong under the new retrieval flow
- thesis retrieval became more inspectable, with clearer planner and region metrics showing where future tuning is needed

## Day 12: Planner Recovery And Bounded Evidence Selection

- tightened query-shape handling for broad summary and specific implementation/detail questions
- improved topology region selection with early-vs-late summary bias
- added low-value synopsis suppression for bibliography/manuscript-style noise
- bumped the index schema to rebuild topology artifacts cleanly
- added a bounded post-rerank evidence selector:
  - only reviews top retrieved candidates
  - keeps a rank prior
  - can still promote lower-ranked but more direct evidence

Challenges:

- planner changes can easily help one broad-question family while hurting another
- broad paper-summary questions remain harder than clause-style or exact factual questions
- extra LLM help had to stay tightly bounded so latency and instability did not spread through the whole pipeline

Improvements:

- thesis recovered to a stronger retrieval snapshot than the earlier pre-topology baseline
- health stayed stable
- `pancreas7` kept its gain
- the evidence selector now fixes some cases where the correct evidence was already in top `k` but not rank 1

## Day 13: Low-Confidence Structure Repair And Dedicated Global Summaries

- Added a low-confidence structure-repair layer at indexing time for noisy journal PDFs.
- Kept deterministic section extraction first and only invoked a small model on suspicious documents.
- Added retrieval and negative eval datasets for:
  - `reportgeneration`
  - `reportgeneration2`
- Added a dedicated `global_summary_first` route for broad paper-summary questions.
- Added summary-aware prompt shaping for the final answer model without changing factual-answer behavior.

Challenges:

- some journal-style papers flattened section structure badly enough that synopsis/topology retrieval inherited the wrong document map
- broad questions like `What is this paper about?` were still failing even when relevant chunks were already in the candidate set
- the new summary improvements had to avoid harming the benchmarked policy, thesis, and pancreas document families

Improvements:

- indexing-time structure repair improved section quality only where needed and kept extra model cost out of the live query path
- `reportgeneration` broad-summary behavior improved materially
- `reportgeneration2` main-contribution behavior recovered
- the four older benchmark docs stayed stable or slightly better after the summary-route work

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

## Day 16: Product-Surface Polish And Frontend Handoff Prep

- Improved the Streamlit app surface with:
  - document status panels
  - index status context
  - style-aware starter questions
  - benchmark snapshot tab
  - richer evidence presentation and retrieval debug details
- Rebased the project documentation around the current system state:
  - benchmarked RAG core
  - simplified eval policy
  - frontend as the next major phase

Challenges:

- the backend had become meaningfully stronger than the current UI presentation
- the app still felt more like a research shell than a portfolio-grade product
- several docs still reflected an earlier “Streamlit-first buildout” mindset instead of the current “backend strong, frontend next” state

Improvements:

- the app now does more onboarding and framing work for the user
- benchmark and document-state visibility moved into the product surface instead of living only in repo docs
- the docs now clearly reflect that Helpmate is in a strong enough technical place to invest in a proper frontend

## Day 17: FastAPI And Next.js Product Shell

- Added the first real `FastAPI` boundary in `backend/`.
- Started the custom product UI in `frontend/` with `Next.js`.
- Kept Streamlit available as the internal benchmark and inspection shell.

Challenges:

- the retrieval core had become stronger than the credibility of the original Streamlit-only surface
- the product needed a cleaner public-facing workflow without disturbing the benchmarked backend behavior
- the repo documentation still reflected an earlier Streamlit-first mindset

Improvements:

- the product now has a clear frontend/backend direction
- the Python retrieval core remains reusable instead of being tied to one UI shell
- the repo is better positioned for a more premium presentation layer

## Day 18: Removed LLM Query Rewriting And Added Retrieval Guardrails

- Removed model-based query rewriting from the retrieval path.
- Replaced it with deterministic weak-evidence expansion in `src/retrieval/query_rewriter.py`.
- Added evidence grading with:
  - `strong`
  - `weak`
  - `unsupported`
- Added direct retrieval guardrails so obviously irrelevant questions can fail before answer generation.

Challenges:

- model-based rewrite behavior was variable and made benchmark runs harder to interpret
- broad-question rewrites sometimes helped one document but hurt another
- irrelevant questions could still travel too far through the answer pipeline

Improvements:

- retrieval behavior is now simpler and more predictable
- off-topic questions can now fail fast with a clean guardrail response
- only the weak middle band triggers adaptive retrieval recovery

## Day 19: Stronger Section-First Retrieval Without More Model Layers

- Improved section summaries, aliases, and section-kind aware ranking.
- Added deterministic section seeding for summary-style queries such as:
  - `abstract`
  - `introduction`
  - `overview`
  - `discussion`
  - `conclusion`
  - `future work`
  - `recommendations`
- Added index schema versioning so section representation changes rebuild cleanly.

Challenges:

- broad thesis and paper questions still struggled even after the earlier structure-aware upgrade
- the next fix needed to generalize rather than overfit to one thesis or paper
- Windows/Chroma index reuse can become brittle when index layout changes under active files

Improvements:

- thesis future-work retrieval now resolves through the stronger section path
- `pancreas8` main-focus retrieval now resolves through the stronger section path
- old index layout changes now have a safer rebuild path via schema-versioned storage

## Day 20: Four-Document Benchmark Pass After Retrieval Simplification

- Ran the full benchmark stack again across:
  - health policy
  - thesis
  - `pancreas7`
  - `pancreas8`
- Updated the benchmark summary docs with the latest `ragas` comparisons for:
  - Helpmate
  - OpenAI retrieval + shared answer model
  - Vectara retrieval + shared answer model

Challenges:

- the full benchmark suite is slow because each document triggers multiple eval families and vendor calls
- long wrapper runs can outlive the local tool window even after the reports have already been written

Improvements:

- health-policy performance stayed stable after removing model-based rewrite logic
- `pancreas8` improved materially under the stronger section-first retrieval path
- thesis and `pancreas7` are now the clearest remaining retrieval-quality targets
