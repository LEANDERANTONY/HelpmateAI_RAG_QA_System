# Roadmap

This roadmap reflects the current HelpmateAI state after the Streamlit refactor, benchmark harnesses, and document-intelligence upgrade.

## Now: Stabilize The Retrieval Product

Current baseline:

- Streamlit-first document QA application
- modular `src/` architecture
- PDF and DOCX ingestion
- local-first Chroma persistence
- hybrid retrieval with reranking
- answer caching and explicit abstention
- offline eval datasets and saved benchmark reports
- OpenAI file-search comparison harness
- open-source `ragas` answer-quality evaluation
- lightweight document-intelligence layer for structure-aware retrieval
- dual-path retrieval with section-first support
- lightweight LLM-assisted route selection for low-confidence mixed queries

Highest-priority active work:

- improve retrieval on non-policy documents such as long academic prose
- improve academic-paper section parsing and front-matter/reference suppression
- reduce remaining clause-level misses through better section and clause targeting
- make retrieval reasoning more visible in the app
- expand evaluation beyond retrieval hit-rate by using answer-quality signals and eventually gold-answer datasets
- keep benchmark quality high across multiple document families

Status:

- Active delivery focus

## Next: Better Document Parsing And Retrieval Generalization

- improve academic-paper and thesis section detection
- add richer section summaries and section-level embeddings
- improve query analysis and router behavior beyond current heuristic classes
- better support thesis, report, and research-paper style questions that require broader semantic synthesis
- add tougher eval sets for narrative and cross-section questions
- add optional gold-answer datasets so answer-quality metrics can move beyond no-reference scoring
- compare against additional managed retrieval baselines when credentials are available

Status:

- Planned next quality step on the current architecture

## Later: Product Hardening On The Current Stack

- improve deployment reliability on Docker/Render-style hosting
- reduce noisy runtime behavior such as Chroma telemetry clutter
- add a retrieval-debug or eval dashboard to the UI
- add more operational smoke checks around indexing and benchmarking
- expose benchmark summaries in the UI

Status:

- In progress, but secondary to retrieval quality

## Later: Auth And User Persistence

- Supabase auth
- user-level workspaces or document history
- usage tracking and quotas
- saved benchmark and debugging views per user if the product grows beyond local use

Status:

- Deferred until the retrieval product is stronger

## Future: Backend Extraction

FastAPI remains a future extraction path when the product genuinely needs:

- background indexing jobs
- multi-user concurrency beyond Streamlit comfort
- non-Streamlit clients
- better operational control over long-running tasks

Status:

- Planned, not started

## Separate Future Product: Dissertation Paraphrasing App

- keep the paraphrasing workflow separate from HelpmateAI
- reuse shared document-processing and deployment patterns where useful
- treat document transformation as a sibling product, not as a Helpmate feature branch

Status:

- Intentionally separate and deferred
