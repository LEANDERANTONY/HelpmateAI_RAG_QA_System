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

- In an independent `ragas` answer-quality comparison across health-policy, thesis, and scientific-paper benchmarks, Helpmate outperformed OpenAI File Search on faithfulness, answer relevancy, and context precision in every document family we tested.
- Helpmate also outperformed the stronger Vectara retrieval baseline overall: health policy `0.8462 / 0.5995 / 0.8462` vs Vectara `0.7692 / 0.4773 / 0.8833`, thesis `1.0000 / 0.6310 / 0.8449` vs `0.9167 / 0.6283 / 0.8406`, `pancreas7` `0.8889 / 0.5247 / 0.9599` vs `0.7778 / 0.5045 / 0.7752`, and `pancreas8` `0.8750 / 0.5034 / 0.9222` vs `0.7667 / 0.5052 / 0.6337` for `ragas` faithfulness / answer relevancy / context precision.
- OpenAI File Search was the weakest external baseline in the same `ragas` check: health policy `0.5769 / 0.1531 / 0.5927`, thesis `0.5069 / 0.4299 / 0.5687`, `pancreas7` `0.5556 / 0.3606 / 0.4920`, and `pancreas8` `0.6000 / 0.2221 / 0.4887`.
- Internal ablations still justify the current stack: reranker improved answer-layer supported rate from `0.8026` to `0.8816`, improved citation page-hit rate from `0.6974` to `0.8684`, and planner plus reranker lifted evidence-fragment recall to `0.7364`.
- Bounded evidence selection remains experimental because it increased cost while reducing faithfulness on the current benchmark suite.

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
