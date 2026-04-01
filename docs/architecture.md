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
4. build or reuse a persisted Chroma index
5. analyze the question and retrieve evidence
6. rerank and adapt retrieval when evidence is weak
7. generate a grounded answer with explicit support status
8. cache safe answer results for repeated questions

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

This makes retrieval more explainable and creates a foundation for future section-first or clause-first retrieval.

## Retrieval Stack

HelpmateAI currently uses a hybrid retrieval design:

- dense retrieval from Chroma
- lexical retrieval via TF-IDF scoring
- reciprocal-rank style fusion
- optional cross-encoder reranking
- metadata-aware ranking preferences
- adaptive query rewriting when evidence is weak

The retrieval layer also performs lightweight query analysis so it can classify questions into broad modes such as:

- `definition_lookup`
- `waiting_period_lookup`
- `process_lookup`
- `benefit_lookup`

These classifications are used as soft preferences, not hard routing rules.

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

## Architectural Challenges Encountered

Important issues discovered during implementation:

- early retrieval misses sometimes came from poor benchmark labels rather than poor retrieval
- Chroma emits noisy telemetry warnings in the current environment
- Chroma accepts only scalar metadata values, so rich metadata must be sanitized for index writes
- retrieval quality is stronger on structured policy documents than on long academic prose
- query analysis is currently heuristic and still biased toward clause-like factual questions

## Current Strengths

- clean modular architecture
- local-first inspectability
- explicit abstention
- saved benchmark reports
- document-intelligence layer already integrated into the live retrieval path

## Current Weaknesses

- clause-level misses still happen when relevant content spans adjacent sections
- narrative and synthesis-heavy questions are harder than factual clause lookups
- section-first retrieval has not yet been implemented

## Likely Next Architecture Step

The most justified next improvement is hierarchical retrieval:

- retrieve the right section first
- then retrieve the best clause or chunk inside that section

That would build naturally on the structure and query-analysis layer already added to the repo.
