# Roadmap

This roadmap reflects the current HelpmateAI state after the retrieval architecture work, benchmark-policy refinement, and the first product-polish pass in Streamlit.

## Now: Frontend And Product Presentation

Current baseline:

- strong document QA backend core
- modular `src/` architecture
- PDF and DOCX ingestion
- local-first Chroma persistence
- hybrid retrieval with reranking
- answer caching and explicit abstention
- offline eval datasets and saved benchmark reports
- `ragas` answer-quality evaluation
- Vectara as the primary external retrieval baseline
- OpenAI retained as a historical/reference retrieval baseline
- lightweight document-intelligence layer for structure-aware retrieval
- dual-path retrieval with section-first support
- lightweight LLM-assisted route selection for low-confidence mixed queries
- improved Streamlit UI with:
  - document status panels
  - style-aware starter questions
  - benchmark summary surfaces

Highest-priority active work:

- move from the current Streamlit shell toward a stronger custom frontend
- preserve the benchmarked Python retrieval core while improving product credibility
- make the app feel more like a polished product than a research tool
- keep benchmark quality high while the frontend evolves

Status:

- active delivery focus

## Next: Retrieval Refinement On The Current Core

- improve academic-paper and thesis section detection
- add richer section summaries and section-level embeddings
- improve query analysis and router behavior beyond current heuristic classes
- better support thesis, report, and research-paper style questions that require broader semantic synthesis
- add tougher eval sets for narrative and cross-section questions
- add optional gold-answer datasets so answer-quality metrics can move beyond no-reference scoring
- compare against additional managed retrieval baselines when credentials are available, if they add meaningful value beyond Vectara

Status:

- planned quality step on top of the current architecture

## Later: Frontend Hardening And API Extraction

- extract cleaner API boundaries if the custom frontend needs them
- improve deployment reliability for a split frontend/backend setup if adopted
- keep benchmark and retrieval-debug views accessible in the product UI
- reduce noisy runtime behavior such as Chroma telemetry clutter

Status:

- deferred until the new frontend direction is chosen and scoped

## Later: Auth And User Persistence

- Supabase auth
- user-level workspaces or document history
- usage tracking and quotas
- saved benchmark and debugging views per user if the product grows beyond local use

Status:

- deferred until the retrieval product and frontend are stronger

## Future: Backend Extraction

FastAPI remains the most likely extraction path when the product genuinely needs:

- a custom frontend talking to backend endpoints
- background indexing jobs
- multi-user concurrency beyond Streamlit comfort
- non-Streamlit clients
- better operational control over long-running tasks

Status:

- likely future phase, not yet started

## Separate Future Product: Dissertation Paraphrasing App

- keep the paraphrasing workflow separate from HelpmateAI
- reuse shared document-processing and deployment patterns where useful
- treat document transformation as a sibling product, not as a Helpmate feature branch

Status:

- intentionally separate and deferred
