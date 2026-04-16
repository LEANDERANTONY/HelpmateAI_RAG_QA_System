# HelpmateAI

[![CI](https://github.com/LEANDERANTONY/HelpmateAI_RAG_QA_System/actions/workflows/ci.yml/badge.svg)](https://github.com/LEANDERANTONY/HelpmateAI_RAG_QA_System/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Live App](https://img.shields.io/badge/Live%20App-Vercel-2563eb?logo=vercel&logoColor=white)](https://helpmateai.framer.website)

HelpmateAI is a grounded long-document QA system for PDFs and DOCX files. Upload a policy, thesis, or research paper, ask a question in plain language, and get a readable answer with visible citations and raw supporting evidence.

The current product is a `Next.js + FastAPI` experience on top of a benchmark-driven Python retrieval core, deployed with a VPS-ready backend path. The system is designed to stay inspectable: retrieval is hybrid, answers are citation-aware, and the supporting passages remain visible instead of being hidden behind a polished summary.

Live app: [helpmateai.framer.website](https://helpmateai.framer.website)

## Why This Project Stands Out

- grounded answers instead of generic document chat
- visible citation trail plus raw evidence panels
- structure-aware retrieval for policies, theses, and research papers
- benchmark-driven architecture decisions instead of intuition-only RAG tuning
- a product-facing `Next.js` shell backed by a modular Python core

## Product Preview

### Landing experience

![HelpmateAI landing page](images/helpmate-landing.png)

### Workspace flow

| Workspace | Answer panel |
| --- | --- |
| ![HelpmateAI workspace](images/helpmate-workspace.png) | ![HelpmateAI grounded answer panel](images/helpmate-answer.png) |

### Evidence visibility

![HelpmateAI evidence panel](images/helpmate-evidence.png)

## Core Workflow

1. Upload a PDF or DOCX file.
2. Build or reuse the document index.
3. Ask a natural-language question.
4. Review the answer, citation trail, and raw evidence together.

## Benchmark Highlights

- reranker improved answer-layer supported rate from `0.8026` to `0.8816`
- reranker improved citation page-hit rate from `0.6974` to `0.8684`
- calibrated planner plus reranker slightly improved evidence fragment recall to `0.7364`
- bounded evidence selection remains experimental because it increased cost while reducing faithfulness on the current benchmark suite

## Project Shape

The repo is no longer a notebook demo. It is a real app-shaped project with:

- `frontend/` as the evolving `Next.js` product UI
- `backend/` as the FastAPI boundary over the Python core
- `Dockerfile` as the backend deployment image
- `deploy/vps/` as the primary Docker Compose plus Caddy VPS deployment bundle
- `src/` for reusable ingestion, retrieval, generation, cache, and shared service logic
- `src/structure/`, `src/query_analysis/`, `src/sections/`, and `src/query_router.py` for the document-intelligence and routing layers
- `tests/` for focused fast checks around the core logic
- `docs/` for architecture, evaluation policy, roadmap, and history

## Stack

- Next.js
- FastAPI
- ChromaDB
- optional hosted Chroma-compatible HTTP backend
- optional Supabase-backed state persistence
- OpenAI
- scikit-learn
- sentence-transformers
- `uv` for project and dependency management
