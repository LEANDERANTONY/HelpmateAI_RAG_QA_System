# HelpmateAI

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

HelpmateAI is a Streamlit-first long-document QA app for grounded answers over PDFs and DOCX files. It indexes documents locally, runs hybrid retrieval with reranking, and returns citation-aware answers with visible evidence.

## What It Does

- Upload a long-form PDF or DOCX document
- Build or reuse a persisted local Chroma index keyed by document fingerprint
- Run hybrid retrieval with dense search, lexical search, fusion, and optional cross-encoder reranking
- Apply metadata-aware retrieval, adaptive query rewriting, and weak-evidence re-retrieval when needed
- Infer document structure, content types, section kinds, and clause metadata for smarter semantic chunking and retrieval routing
- Use dual retrieval paths:
  - `chunk_first` for exact factual and clause-style questions
  - `section_first` for broader narrative or synthesis questions
- Use a lightweight LLM-assisted router only when heuristic routing is low-confidence
- Generate grounded answers with citations and surfaced supporting passages
- Reuse conservative answer-cache entries when the document and question context still match
- Evaluate retrieval quality with layered benchmarks under `docs/evals/`
  - custom retrieval hit-rate and MRR
  - abstention checks
  - Vectara and OpenAI hosted retrieval comparison
  - `ragas` faithfulness, answer relevance, and context-precision scoring

## Product Direction

This repository is now structured as an app project rather than a notebook-only demo:

- `app.py` provides the Streamlit entrypoint
- `src/` contains the reusable ingestion, retrieval, generation, cache, and UI code
- `src/structure/` infers section and clause context from documents
- `src/query_analysis/` classifies questions so retrieval can prefer the right evidence type
- `src/sections/` builds reusable section records and summaries for section-first retrieval
- `src/query_router.py` chooses between retrieval paths without turning the app into a full agent system
- `docs/` contains quickstart and architecture notes
- `tests/` contains focused fast tests around reusable logic

The original notebook remains in the repo as a historical reference skeleton, not as the primary implementation surface.

## Stack

- Streamlit
- ChromaDB
- OpenAI
- scikit-learn
- sentence-transformers
- `uv` for project/dependency management

## Quickstart

1. Create a virtual environment and install dependencies with `uv`.
2. Set `OPENAI_API_KEY` if you want live grounded answer generation.
3. Run `streamlit run app.py`.
4. Upload a document and build or reuse the local index.
5. Ask grounded questions and inspect citations/evidence.

`pyproject.toml` and `uv.lock` are the only dependency source of truth.

More detail lives in [docs/quickstart.md](docs/quickstart.md) and [docs/architecture.md](docs/architecture.md).

Additional project history and architecture decisions live in:

- [docs/implementation-history.md](docs/implementation-history.md)
- [docs/adr/README.md](docs/adr/README.md)
- [docs/evals/README.md](docs/evals/README.md)

## Current Scope

- Supported document types: `.pdf`, `.docx`
- Retrieval-first long-document QA
- Local-first indexing and caching
- Dual-path retrieval with heuristic plus lightweight LLM routing

Out of scope for this first structured build:

- auth and quotas
- hosted user persistence
- paraphrasing/document-rewrite workflows
- FastAPI backend extraction
