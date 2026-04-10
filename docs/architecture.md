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
3. repair low-confidence section maps at indexing time when journal-style layout noise is detected
4. create metadata-rich chunks, sections, and deterministic section synopses
5. build or reuse persisted chunk, section, and synopsis indexes plus lightweight topology artifacts
6. analyze the question and produce a deterministic retrieval plan
7. retrieve evidence through chunk-first, synopsis-first, dedicated global-summary retrieval, legacy section-first fallback, or hybrid retrieval
8. grade evidence as `strong`, `weak`, or `unsupported`
9. adapt retrieval through structural guidance and global fallback instead of query rewriting
10. optionally run a bounded post-rerank evidence selector over the top candidates
11. generate a grounded answer with explicit support status
12. cache safe answer results for repeated questions

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

The current retrieval upgrade adds a lightweight topology layer on top of these sections:

- `SectionSynopsisRecord`
- `TopologyEdge`
- generic region kinds such as:
  - `overview`
  - `definitions`
  - `procedure`
  - `evidence`
  - `discussion`
  - `rules`
  - `appendix`

These topology artifacts are stored locally alongside the existing schema-versioned Chroma index rather than in a separate graph database.

For noisy academic and journal PDFs, the indexing path now includes a low-confidence structure-repair step:

- deterministic parsing runs first
- structural confidence is scored from lightweight layout heuristics
- only suspicious documents trigger a small-model repair pass
- repaired section titles, page assignments, and section-role labels feed synopsis and topology generation

This keeps extra model usage out of the live query path while improving structure quality for difficult documents.

## Retrieval Stack

HelpmateAI now uses a planned hybrid retrieval design:

- dense retrieval from Chroma
- lexical retrieval via TF-IDF scoring
- reciprocal-rank style fusion
- optional reranking
- metadata-aware ranking preferences
- deterministic `RetrievalPlan` generation before retrieval
- chunk-first retrieval for exact factual grounding
- synopsis-first hierarchical retrieval for section-level and global questions
- hybrid merge mode when the query is genuinely mixed or distributed
- soft multi-region structural guidance with global fallback
- hard structural constraints only for explicit page, clause, or named-section references

The planner reasons about generic question shape rather than domain-specific taxonomies. It predicts:

- `intent_type`
  - `lookup`
  - `summary`
  - `comparison`
  - `procedure`
  - `numeric`
  - `cross_cutting`
- `evidence_spread`
  - `atomic`
  - `sectional`
  - `distributed`
  - `global`
- `constraint_mode`
  - `none`
  - `soft_local`
  - `soft_multi_region`
  - `hard_region`

Routing can now choose between:

- `chunk_first`
- `synopsis_first`
- `global_summary_first`
- `section_first`
- `hybrid_both`

The planner is deterministic first. A lightweight LLM-assisted route refinement remains available only when planning confidence is low. There is no model-based query rewriting in the current architecture.

## Dedicated Global-Summary Route

Broad questions like:

- `What is this paper about?`
- `What is the main contribution of this paper?`
- `What are the key findings of this paper?`

now use a dedicated evidence-assembly path when the planner marks them as `global`.

This route:

- ranks section synopses first
- selects a small set of anchor sections across:
  - overview-style material
  - findings/results-style material
  - discussion/conclusion-style material when present
- seeds representative chunks from those sections
- adds a bounded global fallback pool
- still answers only from raw chunk evidence

This route exists because broad paper-summary failures were often not true retrieval misses. The system had relevant chunks, but needed a cleaner evidence bundle for the answer stage.

## Evidence Selection Layer

After retrieval and reranking, HelpmateAI can run a bounded evidence selector before answer generation.

Properties:

- only sees the top retrieved candidates
- uses ranking order as a prior, not as an absolute rule
- can promote a lower-ranked candidate when it is clearly more direct than rank 1
- never invents evidence and never bypasses unsupported retrieval guardrails
- is most useful when the correct evidence is already in top `k` but not at rank 1

This layer is intentionally narrower than a planner or rewriter:

- it does not change the query
- it does not retrieve new chunks
- it only chooses the best one or two finalists from the existing retrieval result

## Weak-Evidence And Guardrail Flow

The earlier query rewrite layer has been removed.

Current weak-evidence behavior:

- grade retrieval evidence as `strong`, `weak`, or `unsupported`
- short-circuit obviously irrelevant questions before answer generation
- allow only the `weak` middle band to trigger adaptive structural retrieval
- keep unsupported questions from flowing into answer generation
- keep soft-local and soft-multi-region plans backed by a global fallback pool so recall does not silently collapse

This reduced variability, removed an unnecessary retrieval layer, and made planner behavior measurable in benchmarks.

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
- structure-aware retrieval metrics:
  - `section_hit_rate`
  - `region_hit_rate`
  - `plan_accuracy`
  - `global_fallback_recovery_rate`
  - `multi_region_recall`
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
- topology-aware retrieval behavior across policy, thesis, and research-paper documents

Current benchmark read:

- health-policy retrieval remains stable
- thesis is now recovered and stronger than the earlier pre-topology baseline
- `pancreas7` remains improved under the topology-aware stack
- `pancreas8` remains strong overall, though broad paper-summary retrieval is still the hardest remaining benchmark case
- the new report-generation eval sets show strong retrieval quality overall, but broad paper-summary questions are still more fragile than concrete questions
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
- deterministic retrieval planning is now explicit and inspectable
- chunk-first and synopsis-first retrieval paths are both live
- structure is now an active retrieval control signal rather than passive metadata
- evaluation policy is now simpler and more credible:
  - Vectara as main external retrieval baseline
  - OpenAI as historical/reference retrieval baseline
  - `ragas` as the active answer-quality meter

## Current Weaknesses

- clause-level misses still happen when relevant content spans adjacent sections
- narrative and synthesis-heavy questions are harder than factual clause lookups
- thesis aim/method questions are still weaker than we want
- region-hit metrics are newer than page-hit metrics, so they still need interpretation before they become optimization targets
- the new frontend is still under active buildout, so the product shell is not fully aligned with backend maturity yet

## Likely Next Product Step

The most justified next improvements are now split into two tracks.

Backend-quality track:

- improve the remaining weakest broad-summary cases after the new global-summary route
- refine synopsis ranking and overview/finding balance without overfitting to the current report papers
- better suppression of references, appendices, and front-matter noise
- possible selective expansion of the bounded evidence-selector layer if it continues helping rank-order mistakes

Frontend/product track:

- continue the `Next.js + FastAPI` migration
- keep the existing Python retrieval core intact
- preserve Streamlit as an internal benchmark/debug shell rather than the primary user surface

The architecture now supports these improvements without another major restructure.
