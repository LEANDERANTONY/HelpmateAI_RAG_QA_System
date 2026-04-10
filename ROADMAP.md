# Roadmap

This roadmap reflects the current HelpmateAI state after the document-topology retrieval upgrade, benchmark-policy refinement, bounded evidence selection, low-confidence structure repair, the dedicated global-summary route, and the move toward a `Next.js + FastAPI` product shell.

## Now: Frontend And Product Presentation

Current baseline:

- strong document QA backend core
- modular `src/` architecture
- PDF and DOCX ingestion
- local-first Chroma persistence
- hybrid retrieval with reranking
- answer caching and explicit abstention
- retrieval guardrails with `strong` / `weak` / `unsupported` evidence states
- offline eval datasets and saved benchmark reports
- `ragas` answer-quality evaluation
- Vectara as the primary external retrieval baseline
- OpenAI retained as a historical/reference retrieval baseline
- lightweight document-intelligence layer for structure-aware retrieval
- deterministic retrieval planning plus synopsis-first and hybrid retrieval
- low-confidence indexing-time structure repair for noisy journal PDFs
- dedicated `global_summary_first` retrieval for broad paper-summary questions
- lightweight LLM-assisted route selection for low-confidence mixed queries
- deterministic weak-evidence expansion instead of model-based query rewriting
- bounded post-rerank evidence selection for ambiguous top-k results
- `Next.js + FastAPI` app shell now started and being actively refined
- retained Streamlit shell with:
  - document status panels
  - style-aware starter questions
  - benchmark summary surfaces

Highest-priority active work:

- continue the move from the current Streamlit shell toward a stronger custom frontend
- preserve the benchmarked Python retrieval core while improving product credibility
- make the app feel more like a polished product than a research tool
- keep benchmark quality high while the frontend evolves
- avoid another large retrieval-core rewrite unless the remaining broad-summary edge cases justify it

Status:

- active delivery focus

## Next: Retrieval Refinement On The Current Core

- only revisit broad paper-summary retrieval if the remaining hard cases persist after frontend work starts
- refine section ranking for broad academic questions without reintroducing model-based rewrite variability
- improve planner accuracy and region-hit quality without overfitting to current benchmark files
- better support thesis, report, and research-paper style questions that require broader semantic synthesis
- add tougher eval sets for narrative and cross-section questions
- add optional gold-answer datasets so answer-quality metrics can move beyond no-reference scoring
- compare against additional managed retrieval baselines when credentials are available, if they add meaningful value beyond Vectara

Status:

- planned quality step on top of the current architecture

## Later: Frontend Hardening And API Extraction

- harden the current FastAPI boundary as the frontend grows
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

## Future: Product Hardening

- background indexing jobs
- multi-user concurrency beyond local development comfort
- non-Streamlit clients
- stronger operational control over long-running eval and indexing tasks

Status:

- future hardening phase after the new frontend stabilizes

## Separate Future Product: Dissertation Paraphrasing App

- keep the paraphrasing workflow separate from HelpmateAI
- reuse shared document-processing and deployment patterns where useful
- treat document transformation as a sibling product, not as a Helpmate feature branch

Status:

- intentionally separate and deferred
