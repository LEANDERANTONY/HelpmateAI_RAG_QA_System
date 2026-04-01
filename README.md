# HelpmateAI

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

HelpmateAI is a Streamlit-first long-document QA app for grounded answers over PDFs and DOCX files. It indexes documents locally, runs hybrid retrieval with reranking, and returns citation-aware answers with visible evidence.

## What It Does

- Upload a long-form PDF or DOCX document
- Build or reuse a persisted local Chroma index keyed by document fingerprint
- Run hybrid retrieval with dense search, lexical search, fusion, and optional cross-encoder reranking
- Apply metadata-aware retrieval, adaptive query rewriting, and weak-evidence re-retrieval when needed
- Generate grounded answers with citations and surfaced supporting passages
- Reuse conservative answer-cache entries when the document and question context still match
- Evaluate retrieval quality with a small offline dataset under `docs/evals/`

## Product Direction

This repository is now structured as an app project rather than a notebook-only demo:

- `app.py` provides the Streamlit entrypoint
- `src/` contains the reusable ingestion, retrieval, generation, cache, and UI code
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

## Current Scope

- Supported document types: `.pdf`, `.docx`
- Retrieval-first long-document QA
- Local-first indexing and caching

Out of scope for this first structured build:

- auth and quotas
- hosted user persistence
- paraphrasing/document-rewrite workflows
- FastAPI backend extraction
