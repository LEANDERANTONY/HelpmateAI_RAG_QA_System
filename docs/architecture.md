# Architecture

HelpmateAI is a long-document QA system with backend-ready core services, local-first retrieval infrastructure, and benchmark-driven quality controls. Streamlit is the current presentation layer, but the core is intentionally modular so a stronger custom frontend can become the next product phase without another large backend rewrite.

## Runtime Shape

- `app.py` is the current thin Streamlit launcher
- `src/ui/` owns theming, rendering, state wiring, and the current benchmark/document panels
- `src/pipeline/` coordinates ingestion, indexing, retrieval, and answer generation
- `src/ingest/`, `src/chunking/`, `src/retrieval/`, `src/generation/`, and `src/cache/` remain transport-agnostic

This split now matters because the retrieval core is largely stable, while the next major change is expected to happen in the frontend layer rather than in the retrieval architecture.

## Main Pipeline

1. ingest uploaded PDF or DOCX content
2. infer lightweight document structure
3. create metadata-rich chunks
4. build or reuse persisted chunk and section indexes in Chroma
5. analyze the question and route retrieval
6. retrieve evidence through chunk-first, section-first, or hybrid retrieval
7. rerank and adapt retrieval when evidence is weak
8. generate a grounded answer with explicit support status
9. cache safe answer results for repeated questions

## Ingestion And Structure Layer

The ingestion path now captures more than raw text:

- page labels
- section headings
- clause ids where detectable
- section paths
- section kinds
- content-type hints such as:
  - `definition`
  - `waiting_period`
  - `claims_procedure`
  - `benefit`
  - `exclusion`

This structure is inferred in `src/structure/` and attached to page metadata before chunking.

## Chunking And Section Layer

Chunking started as deterministic page-window chunking and now includes semantic enrichment.

Current chunk metadata includes:

- `source_file`
- `page_label`
- `document_id`
- `section_heading`
- `section_path`
- `clause_ids`
- `primary_clause_id`
- `content_type`
- `section_id`
- `section_kind`

On top of this, HelpmateAI builds `SectionRecord` objects carrying:

- stable `section_id`
- cleaned section title
- section summary
- page labels
- section path
- clause ids
- section-kind metadata such as `Abstract`, `Introduction`, `Results`, `Conclusion`, or `Future Work`

This layer is especially important for theses and research papers, where broad questions often need section-level navigation before exact chunk retrieval.

## Retrieval Stack

HelpmateAI currently uses a dual-path hybrid retrieval design:

- dense retrieval from Chroma
- lexical retrieval via TF-IDF scoring
- reciprocal-rank style fusion
- optional cross-encoder reranking
- metadata-aware ranking preferences
- adaptive query rewriting when evidence is weak
- chunk-first retrieval for exact factual grounding
- section-first retrieval for broad narrative questions
- hybrid merge mode when the query is genuinely mixed

The retrieval layer performs lightweight query analysis so it can classify questions into broad modes such as:

- `definition_lookup`
- `waiting_period_lookup`
- `process_lookup`
- `benefit_lookup`
- `summary_lookup`

These classifications are used as soft retrieval preferences.

On top of that, HelpmateAI has a lightweight query router that chooses between:

- `chunk_first`
- `section_first`
- `hybrid_both`

The router is primarily heuristic. A lightweight LLM-assisted tie-breaker is available only when the heuristic router is low-confidence. This remains a deterministic staged pipeline, not a full multi-agent system.

## Answer Generation

Answer generation is grounded on retrieved evidence and uses a structured output contract.

Important properties:

- explicit `supported` versus unsupported answer state
- citations and citation details
- retrieval notes visible to the UI
- conservative abstention when evidence is weak

## Caching

Two conservative caches are active.

Index cache:

- keyed by document fingerprint
- skips unnecessary re-ingestion and re-embedding

Answer cache:

- keyed by fingerprint, normalized question, retrieval version, generation version, and model
- reuses only safe matching answers

## Evaluation And Benchmarking

Evaluation is now a first-class part of the architecture.

Current evaluation surfaces:

- positive retrieval eval datasets
- negative abstention eval datasets
- saved JSON benchmark reports under `docs/evals/reports/`
- Vectara retrieval comparison harness as the primary external baseline
- OpenAI File Search comparison harness kept as a historical/reference baseline
- `ragas` answer-quality evaluation:
  - faithfulness
  - answer relevancy
  - no-reference context precision
- shared-answer `ragas` comparisons on top of OpenAI and Vectara retrieval contexts

This lets the team compare:

- policy-style documents versus thesis-style documents
- local RAG versus hosted retrieval
- retrieval quality versus answer quality
- structural changes versus baseline behavior
- dual-path retrieval behavior across policy, thesis, and research-paper documents

## UI And Product Surface

The current Streamlit surface now includes:

- document status and index status panels
- style-aware starter questions
- answer support badges
- retrieval evidence cards with section/context hints
- benchmark snapshots and benchmark-policy notes inside the app

This is enough for strong iteration and portfolio demos, but it also clarified the next major product need: a custom frontend would better match the maturity of the retrieval core.

## Current Strengths

- clean modular architecture
- local-first inspectability
- explicit abstention
- saved benchmark reports
- document-intelligence layer already integrated into the live retrieval path
- dual chunk-first and section-first retrieval paths are live
- section-aware summaries improved thesis and review-paper retrieval without hurting policy benchmarks
- evaluation policy is now simpler and more credible:
  - Vectara as main external retrieval baseline
  - OpenAI as historical/reference retrieval baseline
  - `ragas` as the active answer-quality meter

## Current Weaknesses

- clause-level misses still happen when relevant content spans adjacent sections
- narrative and synthesis-heavy questions are harder than factual clause lookups
- academic paper section extraction still needs refinement for “main focus” and future-work style questions
- the current Streamlit frontend is still more functional than premium, and no longer fully reflects the strength of the backend core

## Likely Next Product Step

The most justified next improvements are now split into two tracks.

Backend-quality track:

- stronger academic-document parsing and section cleanup
- better suppression of references, appendices, and front-matter noise
- deeper section-aware reranking for broad paper and thesis questions

Frontend/product track:

- move to a more custom and credible frontend experience
- keep the existing Python retrieval core intact
- optionally extract API boundaries later if the custom frontend needs them

The architecture now supports these improvements without another major restructure.
