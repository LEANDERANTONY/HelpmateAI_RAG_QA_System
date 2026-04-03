# HelpmateAI

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

HelpmateAI is a grounded long-document QA system for PDFs and DOCX files. It indexes documents locally, runs hybrid retrieval with reranking, and returns citation-aware answers with visible evidence. The product is now moving onto a `Next.js + FastAPI` surface while the Python retrieval and generation core stays modular and benchmark-driven.

## What It Does

- uploads long-form PDF and DOCX documents
- builds or reuses a persisted local Chroma index keyed by document fingerprint
- runs hybrid retrieval with dense search, lexical search, fusion, and optional reranking
- infers document structure, section kinds, clause metadata, and content-type hints
- uses dual retrieval paths:
  - `chunk_first` for exact factual and clause-style questions
  - `section_first` for broader narrative or synthesis questions
- uses a lightweight LLM-assisted router only when heuristic routing is low-confidence
- uses deterministic adaptive retrieval expansion for weak-evidence cases instead of LLM query rewriting
- short-circuits obviously irrelevant questions with retrieval guardrails before generation
- generates grounded answers with citations, evidence panels, and explicit supported/unsupported status
- evaluates retrieval quality with a layered benchmark stack:
  - custom retrieval hit-rate and MRR
  - abstention checks
  - Vectara as the primary external retrieval baseline
  - OpenAI File Search as a historical/reference retrieval baseline
  - `ragas` as the main answer-quality metric

## Current State

The repo is no longer a notebook demo. It is a real app-shaped project with:

- `frontend/` as the evolving `Next.js` product UI
- `backend/` as the FastAPI boundary over the Python core
- `app.py` as the retained Streamlit research and benchmark shell
- `src/` for reusable ingestion, retrieval, generation, cache, and UI logic
- `src/structure/`, `src/query_analysis/`, `src/sections/`, and `src/query_router.py` for the document-intelligence and routing layers
- `tests/` for focused fast checks around the core logic
- `docs/` for architecture, evaluation policy, roadmap, and history

The original notebook remains only as a historical reference.

## Why It Is In A Good Spot

The RAG core is already in a strong position:

- it generalizes across policy documents, theses, and research papers
- it competes well against external retrieval baselines
- it has a cleaner evaluation story now:
  - Vectara for external retrieval comparison
  - `ragas` for answer-quality comparison
- it has reached the point where frontend/product polish is a better next investment than another large retrieval-core rewrite

## Current Product Direction

HelpmateAI is at the start of a new phase:

- the backend retrieval system is stable enough to keep
- the product shell is shifting to `Next.js + FastAPI`
- Streamlit remains useful for fast iteration, demos, and benchmark visibility, but it is now a secondary shell rather than the main product direction

## Stack

- Next.js
- FastAPI
- Streamlit
- ChromaDB
- OpenAI
- scikit-learn
- sentence-transformers
- `uv` for project and dependency management

## Quickstart

1. Install Python dependencies with `uv` and frontend dependencies with `npm install` in [frontend](C:\Users\Leander Antony A\Documents\Projects\HelpmateAI_RAG_QA_System\frontend).
2. Set `OPENAI_API_KEY` in `.env` if you want live answer generation and evaluation.
3. Run the backend: `uv run uvicorn backend.main:app --reload --port 8001`.
4. Run the frontend: `npm run dev` in [frontend](C:\Users\Leander Antony A\Documents\Projects\HelpmateAI_RAG_QA_System\frontend).
5. Optionally run `streamlit run app.py` for the internal benchmark/debug shell.

`pyproject.toml` and `uv.lock` are the dependency source of truth.

## Important Docs

- [docs/architecture.md](docs/architecture.md)
- [docs/evals/README.md](docs/evals/README.md)
- [docs/evals/benchmark_summary.md](docs/evals/benchmark_summary.md)
- [docs/frontend-reference.md](docs/frontend-reference.md)
- [docs/implementation-history.md](docs/implementation-history.md)
- [docs/adr/README.md](docs/adr/README.md)
- [ROADMAP.md](ROADMAP.md)
- [DEVLOG.md](DEVLOG.md)

## Current Scope

- supported document types: `.pdf`, `.docx`
- retrieval-first long-document QA
- local-first indexing and caching
- dual-path retrieval with heuristic plus lightweight LLM routing
- deterministic weak-evidence expansion instead of model-based query rewriting
- benchmark-aware product surface in both the retained Streamlit shell and the newer frontend/backend app flow

Out of scope for the current phase:

- auth and quotas
- hosted user persistence
- paraphrasing/document-rewrite workflows
- full production hardening for multi-user hosted deployment
