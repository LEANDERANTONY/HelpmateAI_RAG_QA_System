# Architecture

HelpmateAI is a long-document QA system with a benchmarked Python retrieval core, local-first indexing, and explicit quality controls. The current product direction is a `Next.js + FastAPI` surface over the existing Python services, while Streamlit remains available as an internal benchmark and inspection shell.

## Runtime Shape

- `frontend/` owns the new `Next.js` product UI
- `backend/` exposes the main upload, index, status, and ask API boundary
- `app.py` remains a thin Streamlit launcher for internal benchmarking and debug workflows
- `src/pipeline/` coordinates ingestion, indexing, retrieval, and answer generation
- `src/ingest/`, `src/chunking/`, `src/retrieval/`, `src/generation/`, and `src/cache/` remain transport-agnostic

This split now matters because the retrieval core is largely stable, while the main product work is moving into the frontend layer rather than another large backend rewrite.

## Main Pipeline

1. ingest uploaded PDF or DOCX content
2. infer lightweight document structure and document style
3. create metadata-rich chunks and sections
4. build or reuse persisted chunk and section indexes in Chroma
5. analyze the question and route retrieval
6. retrieve evidence through chunk-first, section-first, or hybrid retrieval
7. grade evidence as `strong`, `weak`, or `unsupported`
8. adapt retrieval only for the weak middle band
9. generate a grounded answer with explicit support status
10. cache safe answer results for repeated questions

## Ingestion And Structure Layer

The ingestion path captures more than raw text:

- page labels
- section headings
- clause ids where detectable
- section paths
- section kinds
- document-style hints such as:
  - `policy_document`
  - `thesis_document`
  - `research_paper`
  - `generic_longform`
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
- `document_style`

On top of this, HelpmateAI builds `SectionRecord` objects carrying:

- stable `section_id`
- cleaned section title
- section summary
- page labels
- section path
- clause ids
- section kind
- section aliases for summary-style retrieval

This layer is especially important for theses and research papers, where broad questions often need section-level navigation before exact chunk retrieval.

## Retrieval Stack

HelpmateAI currently uses a dual-path hybrid retrieval design:

- dense retrieval from Chroma
- lexical retrieval via TF-IDF scoring
- reciprocal-rank style fusion
- optional reranking
- metadata-aware ranking preferences
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

The router is primarily heuristic. A lightweight LLM-assisted tie-breaker is still available only when the heuristic router is low-confidence. This remains a deterministic staged pipeline, not a full multi-agent system.

## Weak-Evidence And Guardrail Flow

The earlier model-based query rewrite layer has been removed.

Current weak-evidence behavior:

- grade retrieval evidence as `strong`, `weak`, or `unsupported`
- short-circuit obviously irrelevant questions before answer generation
- allow only the `weak` middle band to retry retrieval
- use deterministic query expansion instead of LLM query rewriting
- expand weak summary questions with section-aware variants such as:
  - `abstract`
  - `introduction`
  - `overview`
  - `discussion`
  - `conclusion`
  - `future work`
  - `recommendations`
- keep evidence scoring anchored to the original user question rather than any expanded retrieval variant

This reduced variability, removed an unnecessary LLM dependency from the retrieval path, and made benchmark behavior easier to interpret.

## Answer Generation

Answer generation is grounded on retrieved evidence and uses a structured output contract.

Important properties:

- explicit `supported` versus unsupported answer state
- citations and citation details
- retrieval notes visible to the UI
- conservative abstention when evidence is weak or unsupported

## Caching And Index Versioning

Two conservative caches are active.

Index cache:

- keyed by document fingerprint
- schema-versioned so structure changes can rebuild cleanly
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

Current benchmark read:

- health-policy retrieval remains stable
- `pancreas8` improved materially with the stronger section-first path
- thesis and `pancreas7` remain the main targets for future retrieval refinement
- OpenAI is still the weakest external retrieval baseline on the current document families

## UI And Product Surface

The current product surface is split across two shells.

Streamlit currently includes:

- document status and index status panels
- style-aware starter questions
- answer support badges
- retrieval evidence cards with section/context hints
- benchmark snapshots and benchmark-policy notes

The `Next.js + FastAPI` surface is now the main product direction for:

- upload and ask workflows
- cleaner landing and workspace presentation
- a more credible product-facing UI

## Current Strengths

- clean modular architecture
- local-first inspectability
- explicit abstention and retrieval guardrails
- saved benchmark reports
- document-intelligence layer integrated into the live retrieval path
- dual chunk-first and section-first retrieval paths are live
- deterministic weak-evidence expansion is simpler and more predictable than the earlier model-rewrite path
- evaluation policy is now simpler and more credible:
  - Vectara as main external retrieval baseline
  - OpenAI as historical/reference retrieval baseline
  - `ragas` as the active answer-quality meter

## Current Weaknesses

- clause-level misses still happen when relevant content spans adjacent sections
- narrative and synthesis-heavy questions are harder than factual clause lookups
- thesis and `pancreas7` aim/method questions are still weaker than we want
- the new frontend is still under active buildout, so the product shell is not fully aligned with backend maturity yet

## Likely Next Product Step

The most justified next improvements are now split into two tracks.

Backend-quality track:

- stronger thesis and paper aim/method retrieval
- better suppression of references, appendices, and front-matter noise
- deeper section-aware reranking for broad paper and thesis questions

Frontend/product track:

- continue the `Next.js + FastAPI` migration
- keep the existing Python retrieval core intact
- preserve Streamlit as an internal benchmark/debug shell rather than the primary user surface

The architecture now supports these improvements without another major restructure.
