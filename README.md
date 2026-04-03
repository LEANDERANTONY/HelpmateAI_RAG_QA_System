# HelpmateAI

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

HelpmateAI is a grounded long-document QA system for PDFs and DOCX files. It indexes documents locally, runs hybrid retrieval with reranking, and returns citation-aware answers with visible evidence. The current product shell is built in Streamlit, while the core retrieval and generation services are kept modular so the next major phase can move to a stronger custom frontend.

## What It Does

- uploads long-form PDF and DOCX documents
- builds or reuses a persisted local Chroma index keyed by document fingerprint
- runs hybrid retrieval with dense search, lexical search, fusion, and optional reranking
- infers document structure, section kinds, clause metadata, and content-type hints
- uses dual retrieval paths:
  - `chunk_first` for exact factual and clause-style questions
  - `section_first` for broader narrative or synthesis questions
- uses a lightweight LLM-assisted router only when heuristic routing is low-confidence
- generates grounded answers with citations, evidence panels, and explicit supported/unsupported status
- evaluates retrieval quality with a layered benchmark stack:
  - custom retrieval hit-rate and MRR
  - abstention checks
  - Vectara as the primary external retrieval baseline
  - OpenAI File Search as a historical/reference retrieval baseline
  - `ragas` as the main answer-quality metric

## Current State

The repo is no longer a notebook demo. It is a real app-shaped project with:

- `app.py` as the current Streamlit entrypoint
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
- the next major step is a stronger custom frontend
- Streamlit remains useful for fast iteration, demos, and benchmark visibility, but it likely should not be the final presentation layer

## Stack

- Streamlit
- ChromaDB
- OpenAI
- scikit-learn
- sentence-transformers
- `uv` for project and dependency management

## Quickstart

1. Install dependencies with `uv`.
2. Set `OPENAI_API_KEY` in `.env` if you want live answer generation and evaluation.
3. Run `streamlit run app.py`.
4. Upload a document and build or reuse the local index.
5. Ask grounded questions and inspect evidence, retrieval notes, and benchmark snapshots.

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
- benchmark-aware product surface with document status and benchmark panels in the current UI

Out of scope for the current phase:

- auth and quotas
- hosted user persistence
- paraphrasing/document-rewrite workflows
- full FastAPI extraction
