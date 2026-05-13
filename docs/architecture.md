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
5. create metadata-rich chunks, sections, typed artifact candidates, and deterministic section synopses
6. build compact document landmarks for front matter, correspondence, executive summary, definitions, glossary, and volume boundaries when enabled
7. classify suspicious chunks and artifact candidates with the indexing-time chunk semantics layer when enabled
8. build or reuse persisted chunk, section, and synopsis indexes plus lightweight topology artifacts
9. analyze the question and produce a retrieval plan, with bounded LLM orchestration for explicit local scope
10. retrieve evidence through chunk-first, synopsis-first, dedicated global-summary retrieval, legacy section-first fallback, or hybrid retrieval
11. grade evidence as `strong`, `weak`, or `unsupported`
12. adapt retrieval through structural guidance and global fallback instead of query rewriting
13. optionally run a reorder-only post-rerank evidence selector over the top candidates when the spread-trigger policy fires
14. generate a grounded answer with explicit support status
15. write an ephemeral workflow trace for uncached QA runs
16. cache safe answer results for repeated questions

## Ingestion And Structure Layer

The ingestion path captures more than raw text. PDF and DOCX extraction run through predictable local backends, with a selective table-enrichment pass for PDFs:

- `HELPMATE_PDF_EXTRACTOR=pypdf` is the default for PDFs and uses the lightweight local text extractor
- `HELPMATE_DOCX_EXTRACTOR=python-docx` is the default for DOCX files
- `HELPMATE_TABLE_EXTRACTOR=pdfplumber` is the default table-enrichment path for likely table-heavy PDF pages
- `HELPMATE_TABLE_EXTRACTOR=off` disables table enrichment
- `HELPMATE_TABLE_EXTRACTOR_MAX_PAGES=40` caps how many candidate pages are reviewed by pdfplumber

`pypdf` and `python-docx` stay as the production defaults because they are fast, local, and fail predictably on large reports. Full-text extraction with pdfplumber was tested on policy, thesis, FOMC, and technical-report PDFs, but it was generally slower and sometimes worse for prose and front matter. Instead, pdfplumber is used only where it helps most: extracting table artifacts from pages that already look numeric, tabular, or captioned.

Managed cloud layout parsers and Docling were tested as candidates for table and heading extraction, but they added too much latency, operational complexity, or install/runtime weight for the current product path. The selected text backend and table-enrichment backend are recorded in document and page metadata so extraction behavior is visible in traces and eval reports.

The important boundary is that deterministic extraction proposes artifact candidates; it does not decide that they are answer-worthy. The chunk semantics layer reviews suspicious chunks and artifact candidates at indexing time and can label them as `metadata_evidence`, `definition_evidence`, `table_evidence`, normal evidence, or noise. Retrieval then consumes those semantic labels instead of relying on document-specific artifact score thresholds.

The document landmark layer handles regions that are easy to under-rank in ordinary chunk retrieval: title pages, forewords, prefaces, author/correspondence blocks, abstracts, executive summaries, definition/glossary regions, and volume boundaries. It reviews only a bounded set of likely structural pages and emits compact page-linked landmark chunks with key facts and source snippets. These landmarks remain ordinary retrieval candidates; no query-specific fact or document-specific page is hardcoded.

After extraction, the ingestion path captures:

- page labels
- section headings
- clause ids where detectable
- section paths
- section kinds
- page-linked table artifacts from pdfplumber when available
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

The indexing layer also preserves noisy but important document artifacts as typed retrieval candidates instead of letting them pollute normal prose retrieval. Tables, footnotes, front matter, definition/acronym snippets, document landmarks, and bibliography blocks are retained with page-linked metadata. The semantic chunk classifier decides whether each candidate is useful evidence, metadata evidence, definition evidence, table evidence, or noise. Normal chunks receive `page_artifact_counts` and `page_artifact_ids` metadata so retrieval traces can show that related artifacts were available without forcing those artifacts into every query.

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
- semantic artifact labels for tables, footnotes, front matter, definitions, and bibliographies
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

Hard local scope disables global fallback and filters final evidence after reranking. Broad questions still remain broad unless the orchestrator gives a valid, high-confidence local boundary. A named section/chapter hint is treated as hard only when the selected section labels, aliases, chapter metadata, or explicit page/clause filters actually match the user's local boundary. If the local hint is real but the match is too coarse, the same sections are kept as soft guidance so retrieval can still recover stronger evidence elsewhere.

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

- explicit `support_status` values: `supported`, `partial`, and `unsupported`
- strict `supported=true` only when every required fact is directly covered by evidence
- `partial` answers only when evidence supports a substantive part of the question and the answer itself names or acknowledges the missing required fact
- a bounded support-status verifier for non-supported first-pass answers; it can recover `supported` only when all required facts are grounded and no gap or inferential wording remains, otherwise it preserves `partial` or `unsupported`
- citations and citation details
- retrieval notes visible to the UI
- conservative abstention when evidence is weak or unsupported

This keeps faithfulness guardrails intact while avoiding a false binary between a complete answer and total refusal. Partial answers remain `supported=false` in the strict boolean field so older metrics continue to mean "fully supported"; eval reports additionally track answerable coverage and partial rate.

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

- the 150-question held-out product-fit suite is the current public evaluation marker
- after the verifier-policy correction, HelpmateAI's estimated answerable coverage is `92.6%`, strict fully supported rate is `89.6%`, and false abstention is `7.4%`
- HelpmateAI kept `0.0%` false support and `100.0%` abstention on intentionally unsupported questions in this suite
- OpenAI File Search and Vectara remain useful native-mode baselines; in the saved runs they have higher answerable-supported rates, but also higher false-support rates
- broad/local/comparison questions are now mostly healthy; the remaining misses are concentrated in tiny metadata/footer facts, forewords/acknowledgements, abbreviation definitions, bibliography-confusable identifiers, and table-heavy numeric evidence

## UI And Product Surface

The current product surface is centered on the `Next.js + FastAPI` workspace, plus a Next.js marketing landing that shares the same Vercel deployment.

The active app carries:

- upload and ask workflows
- Google/Supabase sign-in
- one active document per user with resumable `24h` sliding retention
- LLM-generated starter questions per active document
- answer support states, `support_summary` qualifiers, and per-turn citation pills
- a three-zone workspace shell with the document strip on the left, the chat / answer column in the center, and the evidence rail on the right
- a Read Mode posture that opens the cited PDF side-by-side with the answer on desktop and as a draggable bottom sheet on mobile
- direct-to-API upload support for larger files
- a credible deployed product boundary at `app.helpmateai.xyz` with the same code serving the apex landing at `helpmateai.xyz`

The workspace and the landing share a unified design-token system scoped under `.h-shell`, with the landing route group adding a `.l-shell` scope for marketing-specific styling. A host-based rewrite in `next.config.ts` redirects apex requests to `/landing/*` so a single Vercel project handles both surfaces.

### Source Viewer Endpoint

`GET /documents/{document_id}/file` serves the document for in-app rendering and direct download:

- auth-gated by the standard Supabase JWT pattern
- streams the file via Starlette's `FileResponse` with HTTP `Range` support for PDF.js progressive rendering
- defaults to `Content-Disposition: inline` and returns the viewable PDF rendition
- under `?download=1`, returns the original source format (PDF or DOCX) as an attachment
- returns `415 Unsupported Media Type` when a legacy DOCX record lacks a rendition, so the frontend can fall back to a "download to view" affordance

The endpoint is the only non-trivial binary-data path in the API. Caddy passes range headers through unchanged.

### DOCX Rendition Pipeline

DOCX uploads run through a LibreOffice headless conversion at ingest to produce a PDF rendition the viewer can render:

- the conversion utility lives at `src/ingest/docx_to_pdf.py` and shells out to `libreoffice --headless --convert-to pdf` with a configurable timeout
- the Docker base image installs `libreoffice-core` and `libreoffice-writer` (roughly 400MB), not the full LibreOffice suite, to keep the production image lean
- conversion failure is tolerant: a corrupted DOCX still indexes from extracted text and only loses the inline viewer
- uploaded files are renamed to `{document_id}{ext}` on disk for collision-safe storage and a `viewable_pdf_path` field on the document record points at the rendition
- the retention sweeper whitelists both the source and the rendition

PDF uploads alias their source path as the viewable, so the runtime path is identical for both formats from the viewer's perspective.

### Frontend Error Handling

The frontend ships a structured error pipeline that turns transport, auth, and validation failures into actionable toasts rather than generic stack traces or silent dropouts:

- a typed `ApiError` class with `status`, `detail`, `retriable`, and `retryAfterSeconds` flows from the central fetch wrapper
- a status × operation message map returns `{title, body, action}` for every reasonable combination, so a 5xx during upload reads differently from a 404 on `/qa`
- `sonner`-based toasts surface transient errors; an inline `<ErrorState>` covers the only persistent case (an index-failed workspace)
- React error boundaries at `src/app/error.tsx` and `src/app/global-error.tsx` cover render-time crashes
- retries are wired through closures so each catch carries the original action back, including the original question for ask-retries
- offline detection uses `navigator.onLine` so a connection failure is named correctly rather than reported as a 5xx

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
  - fixed held-out product-fit manifest as the public marker
  - OpenAI File Search and Vectara native-mode baselines
  - `ragas` as the active answer-quality meter
  - explicit separation of full support, partial support, false abstention, and false support

## Current Weaknesses

- title-page, footer, and header metadata can lose to semantically similar bibliography entries
- foreword, acknowledgement, and credits pages are still easy to under-rank for atomic lookup questions
- acronym and definition lookups need better artifact representation when the definition is outside normal prose flow
- table-heavy numeric questions still depend on preserving row, column, unit, and caption context more cleanly at ingestion time
- partial support is deliberately conservative and does not count as full support
- the latest verifier correction has targeted validation; a full rerun should be done before treating the corrected support-status estimate as a permanent benchmark

## Likely Next Product Step

The most justified next backend improvement is artifact-aware ingestion and retrieval precision:

- preserve title-page/footer/header identity separately from bibliography references
- strengthen foreword, acknowledgement, credits, and correspondence landmarks
- improve acronym/definition artifacts without query-specific hardcoding
- keep table rows tied to captions, units, page labels, and surrounding prose
- rerun the 150-question suite after artifact-ingestion changes, then rerun OpenAI and Vectara only when the benchmark story materially changes

Frontend/product track:

- continue refining the `Next.js + FastAPI` product shell
- keep the existing Python retrieval core intact
- keep benchmark and retrieval-debug visibility available without reintroducing a second UI stack
- harden product ergonomics around larger uploads, auth, and user-scoped resume behavior

The architecture now supports these improvements without another major restructure.
