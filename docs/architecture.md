# Architecture

HelpmateAI is a Streamlit-first long-document QA app with backend-ready core services, local-first retrieval infrastructure, and benchmark-driven quality controls.

## Runtime Shape

- `app.py` is a thin Streamlit launcher.
- `src/ui/` owns theming, rendering, and state wiring.
- `src/pipeline/` coordinates ingestion, indexing, retrieval, and answer generation.
- `src/ingest/`, `src/chunking/`, `src/retrieval/`, `src/generation/`, and `src/cache/` remain transport-agnostic so a later FastAPI extraction is possible.

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

The ingestion path now does more than text extraction.

It captures:

- page labels
- section headings
- clause ids where detectable
- section paths
- content-type hints such as:
  - `definition`
  - `waiting_period`
  - `claims_procedure`
  - `benefit`
  - `exclusion`

This structure is inferred in `src/structure/` and then attached to page metadata before chunking.

## Chunking Strategy

Chunking began as deterministic page-window chunking and has since been upgraded with semantic enrichment.

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

This metadata now powers the live dual-retrieval design rather than only acting as future scaffolding.

## Section Layer

HelpmateAI now builds `SectionRecord` objects on top of page and chunk metadata.

Each section carries:

- a stable `section_id`
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

On top of that, HelpmateAI now has a lightweight query router that decides whether the question should use:

- `chunk_first`
- `section_first`
- `hybrid_both`

The router is primarily heuristic. A lightweight LLM-assisted tie-breaker is available only when the heuristic router is low-confidence. This is intentionally not a full multi-agent system.

## Answer Generation

Answer generation is grounded on retrieved evidence and now follows a structured output contract.

Important properties:

- explicit `supported` versus unsupported answer state
- citations and citation details
- retrieval notes visible to the UI
- conservative abstention when evidence is weak

This design was added because unsupported answers needed stronger discipline than free-form answer text alone could provide.

## Caching

Two conservative caches are active:

Index cache:

- keyed by document fingerprint
- skips unnecessary re-ingestion and re-embedding

Answer cache:

- keyed by fingerprint, normalized question, retrieval version, generation version, and model
- reuses only safe matching answers

## Evaluation And Benchmarking

Evaluation is now a first-class part of the architecture, not an afterthought.

Current evaluation surfaces:

- positive retrieval eval datasets
- negative abstention eval datasets
- saved JSON benchmark reports under `docs/evals/reports/`
- OpenAI file-search comparison harness

This lets the team compare:

- policy-style documents versus thesis-style documents
- local RAG versus hosted retrieval
- structural changes versus baseline behavior
- dual-path retrieval behavior across policy, thesis, and research-paper documents

## Architectural Challenges Encountered

Important issues discovered during implementation:

- early retrieval misses sometimes came from poor benchmark labels rather than poor retrieval
- Chroma emits noisy telemetry warnings in the current environment
- Chroma accepts only scalar metadata values, so rich metadata must be sanitized for index writes
- retrieval quality is stronger on structured policy documents than on long academic prose
- query analysis is currently heuristic and still biased toward clause-like factual questions
- academic-paper parsing is still imperfect, especially for front matter, appendices, and bibliography-heavy pages
- the lightweight LLM router is useful as a tie-breaker, but it is not yet a universal quality boost

## Current Strengths

- clean modular architecture
- local-first inspectability
- explicit abstention
- saved benchmark reports
- document-intelligence layer already integrated into the live retrieval path
- dual chunk-first and section-first retrieval paths are now live
- section-aware summaries improved thesis and review-paper retrieval without hurting policy benchmarks

## Current Weaknesses

- clause-level misses still happen when relevant content spans adjacent sections
- narrative and synthesis-heavy questions are harder than factual clause lookups
- academic paper section extraction still needs refinement for “main focus” and future-work style questions

## Likely Next Architecture Step

The most justified next improvements are:

- stronger academic-document parsing and section cleanup
- better suppression of references, appendices, and front-matter noise
- deeper section-aware reranking for broad paper and thesis questions

The architecture now supports these improvements without another major restructure.
