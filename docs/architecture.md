# Architecture

HelpmateAI is a long-document QA system with a benchmarked Python retrieval core, local-first indexing, and explicit quality controls. The current product is a `Next.js + FastAPI` surface over the existing Python services, with the repo standardized around the live frontend plus VPS-backed API shape.

## Runtime Shape

- `frontend/` owns the new `Next.js` product UI
- `backend/` exposes the main upload, index, status, and ask API boundary
- Framer is the intended marketing front door, separate from the deployed product runtime
- the deployment target is a split `app` + `api` shape rather than a single prototype shell
- `src/pipeline/` coordinates ingestion, indexing, retrieval, and answer generation
- `src/ingest/`, `src/chunking/`, `src/retrieval/`, `src/generation/`, and `src/cache/` remain transport-agnostic

This split now matters because the retrieval core is largely stable, while the main product work is moving into the frontend layer rather than another large backend rewrite.

Recommended deployment shape:

- `www` -> Framer
- `app` -> `Next.js`
- `api` -> `FastAPI`

## Main Pipeline

1. ingest uploaded PDF or DOCX content through the configured extraction backend
2. infer lightweight document structure and document style
3. repair low-confidence section maps at indexing time when journal-style layout noise is detected
4. enrich sections with generic document profiles such as chapter, role, page range, and scope labels
5. create metadata-rich chunks, sections, and deterministic section synopses
6. build or reuse persisted chunk, section, and synopsis indexes plus lightweight topology artifacts
7. analyze the question and produce a retrieval plan, with bounded LLM orchestration for explicit local scope
8. retrieve evidence through chunk-first, synopsis-first, dedicated global-summary retrieval, legacy section-first fallback, or hybrid retrieval
9. grade evidence as `strong`, `weak`, or `unsupported`
10. adapt retrieval through structural guidance and global fallback instead of query rewriting
11. optionally run a reorder-only post-rerank evidence selector over the top candidates when the spread-trigger policy fires
12. generate a grounded answer with explicit support status
13. write an ephemeral workflow trace for uncached QA runs
14. cache safe answer results for repeated questions

## Ingestion And Structure Layer

The ingestion path captures more than raw text. PDF and DOCX extraction run through configurable backends:

- `HELPMATE_PDF_EXTRACTOR=pypdf` is the default for PDFs and uses the lightweight local text extractor
- `HELPMATE_DOCX_EXTRACTOR=python-docx` is the default for DOCX files
- `HELPMATE_PDF_EXTRACTOR=docling` or `HELPMATE_DOCX_EXTRACTOR=docling` uses the local Docling path explicitly

`pypdf` and `python-docx` stay as the production defaults because they are fast, local, and fail predictably on large reports. Managed cloud layout parsers were tested as candidates for table and heading extraction, but they added too much latency and operational complexity for the current product path. Docling remains available for local experiments but is not the default after large-PDF memory failures in local testing. The selected backend is recorded in document and page metadata so extraction behavior is visible in traces and eval reports.

Docling OCR is disabled by default through `HELPMATE_DOCLING_OCR=false`. This keeps ingestion safe for large born-digital PDFs where OCR can add significant memory pressure. Set `HELPMATE_DOCLING_OCR=true` only when scanned-image PDFs are part of the target workload and the runtime has enough memory. When Docling is enabled, expanded Markdown table output and OCR state are recorded in document and page metadata.

After extraction, the ingestion path captures:

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
- document profile metadata:
  - document section role
  - chapter number and title where inferable
  - page range
  - scope labels

This layer is especially important for theses and research papers, where broad questions often need section-level navigation before exact chunk retrieval.

Policy documents remain part of the semantic indexing path. The current indexing layer recognizes policy-native section concepts such as coverage, benefits, exclusions, claims, waiting periods, eligibility, renewal, definitions, and schedule-of-benefits sections. The important architecture point is that policy documents are not blanket-skipped by semantic refinement; they are reviewed only when structure quality or synopsis quality is weak enough to justify the extra indexing-time model call.

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

The indexing layer also preserves noisy but important document artifacts as typed retrieval entries instead of letting them pollute normal prose retrieval:

- `table` artifacts preserve the complete detected table-like text block from extraction and are favored for numeric, comparison, table, row, column, rate, value, and parameter questions.
- `footnote` artifacts are tied to their parent page and are favored only for note/footnote-style or front-matter lookup questions.
- `front_matter` artifacts preserve title-page, foreword, version, author, funding, review, and similar metadata pages for targeted lookup.
- `bibliography` artifacts are indexed as explicit-only citation backmatter, so references do not dominate ordinary semantic retrieval.

Normal chunks receive `page_artifact_counts` and `page_artifact_ids` metadata so retrieval traces can show that a page had related tables, footnotes, or front-matter artifacts available. This keeps the previous noise controls for contents/references/table fragments, while still making those regions recoverable when the question actually asks for them.

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
- typed artifact gating for tables, footnotes, front matter, and bibliographies
- deterministic `RetrievalPlan` generation before retrieval
- chunk-first retrieval for exact factual grounding
- synopsis-first hierarchical retrieval for section-level and global questions
- hybrid merge mode when the query is genuinely mixed or distributed
- soft multi-region structural guidance with global fallback
- hard structural constraints only for explicit page, clause, or named-section references

The planner reasons about generic question shape rather than domain-specific taxonomies. A bounded retrieval orchestrator can run before the deterministic planner when the question appears to require document-map interpretation, such as a local chapter or section scope. It receives a compact section map, returns strict JSON, and can only enforce section IDs that already exist in the index.

Validated orchestration can add:

- `allowed_section_ids`
- `scope_strictness`
- `scope_query`
- `answer_focus`
- `orchestrator_reason`

Hard local scope disables global fallback and filters final evidence after reranking. Broad questions still remain broad unless the orchestrator gives a valid, high-confidence local boundary.

The structured plan predicts:

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
- currently runs in reorder-only mode rather than prune mode
- by default triggers only on spread-heavy questions rather than all queries
- never invents evidence and never bypasses unsupported retrieval guardrails
- is most useful when the correct evidence is already in top `k` but not at rank 1

This layer is intentionally narrower than a planner or rewriter:

- it does not change the query
- it does not retrieve new chunks
- it only reorders the final evidence list from the existing retrieval result
- it receives orchestration context so it can respect a validated local scope while staying separate from retrieval planning

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

Workflow traces:

- written for uncached QA runs
- store route, plan, scores, candidate IDs, page/section metadata, previews, support status, and citations
- do not copy full document text or the full answer body
- expire with the same workspace retention window locally and in Supabase

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
- scoped retrieval behavior for local chapter/section questions
- trace-retention and trace-safety behavior for workflow observability

Current benchmark read:

- health-policy retrieval remains stable
- thesis is now recovered and stronger than the earlier pre-topology baseline
- `pancreas7` remains improved under the topology-aware stack
- `pancreas8` remains strong overall, though broad paper-summary retrieval is still the hardest remaining benchmark case
- the new report-generation eval sets show strong retrieval quality overall, but broad paper-summary questions are still more fragile than concrete questions
- the smart-indexing/orchestrator branch passed a lean six-case upgrade check with `1.0000` supported rate, `0.9050` faithfulness, `0.6034` answer relevancy, and `0.7500` context precision
- on five shared lean regression cases, the branch improved answer relevancy by `+0.0966` and context precision by `+0.1000` versus `main` while keeping supported rate unchanged
- OpenAI is still the weakest external retrieval baseline on the current document families

## UI And Product Surface

The current product surface is centered on the `Next.js + FastAPI` workspace.

The active app now carries:

- upload and ask workflows
- Google/Supabase sign-in
- one active document per user with resumable `24h` sliding retention
- starter-question guidance tied to the active document
- answer support states, citations, and evidence panels
- direct-to-API upload support for larger files
- cleaner landing and workspace presentation
- a credible deployed product boundary rather than only an internal prototype shell

## Current Strengths

- clean modular architecture
- local-first inspectability
- explicit abstention and retrieval guardrails
- saved benchmark reports
- document-intelligence layer integrated into the live retrieval path
- deterministic retrieval planning is now explicit and inspectable
- chunk-first and synopsis-first retrieval paths are both live
- structure is now an active retrieval control signal rather than passive metadata
- reorder-only evidence selection is now benchmark-validated and active in the default stack
- orchestration-aware scope enforcement has targeted branch validation for local section/chapter questions and lean vendor comparison against OpenAI File Search and Vectara
- ephemeral run traces make workflow decisions inspectable without becoming long-term memory
- live deployment now reflects the benchmarked architecture instead of a separate demo shell
- evaluation policy is now simpler and more credible:
  - Vectara as main external retrieval baseline
  - OpenAI as historical/reference retrieval baseline
  - `ragas` as the active answer-quality meter

## Current Weaknesses

- clause-level misses still happen when relevant content spans adjacent sections
- narrative and synthesis-heavy questions are harder than factual clause lookups
- the broadest paper-summary questions are still the hardest benchmark family
- `reportgeneration2` remains a structure-repair heuristic-gap case
- region-hit metrics are newer than page-hit metrics, so they still need interpretation before they become optimization targets
- answer-quality eval coverage is still deeper on the main four document families than on the newer report-generation sets
- orchestration needs broader held-out coverage before it should be treated as a settled default

## Likely Next Product Step

The most justified next improvements are now split into two tracks.

Backend-quality track:

- add answer-quality eval coverage for the newer report-generation papers
- add gold-answer coverage for a selected subset of benchmark questions
- expand scoped retrieval eval beyond the thesis-local cases
- add trace-driven failed-answer review tooling
- extend external vendor comparison only after a materially new retrieval or answer-layer change
- close the remaining structure-repair gap on `reportgeneration2`

Frontend/product track:

- continue refining the `Next.js + FastAPI` product shell
- keep the existing Python retrieval core intact
- keep benchmark and retrieval-debug visibility available without reintroducing a second UI stack
- harden product ergonomics around larger uploads, auth, and user-scoped resume behavior

The architecture now supports these improvements without another major restructure.
