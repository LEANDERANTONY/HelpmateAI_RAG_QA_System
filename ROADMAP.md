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
- lightweight document-intelligence layer for structure-aware retrieval

Highest-priority active work:

- improve retrieval on non-policy documents such as long academic prose
- reduce clause-level misses through better section and clause targeting
- make retrieval reasoning more visible in the app
- keep benchmark quality high across multiple document families

Status:

- Active delivery focus

## Next: Hierarchical Retrieval And Better Generalization

- move from mostly page-first retrieval to section-first or clause-first retrieval
- add richer section summaries and section-level embeddings
- improve query analysis beyond current heuristic classes
- better support thesis, report, and research-paper style questions that require broader semantic synthesis
- add tougher eval sets for narrative and cross-section questions

Status:

- Planned next architecture step

## Later: Product Hardening On The Current Stack

- improve deployment reliability on Docker/Render-style hosting
- reduce noisy runtime behavior such as Chroma telemetry clutter
- add a retrieval-debug or eval dashboard to the UI
- add more operational smoke checks around indexing and benchmarking

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
