# HelpmateAI

[![CI](https://github.com/LEANDERANTONY/HelpmateAI_RAG_QA_System/actions/workflows/ci.yml/badge.svg)](https://github.com/LEANDERANTONY/HelpmateAI_RAG_QA_System/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Live App](https://img.shields.io/badge/Live%20App-Vercel-2563eb?logo=vercel&logoColor=white)](https://helpmateai.xyz)

HelpmateAI is a grounded long-document QA system for PDFs and DOCX files. Upload a policy, thesis, or research paper, ask a question in plain language, and get a readable answer with visible citations and raw supporting evidence.

The current product is a `Next.js + FastAPI` experience on top of a benchmark-driven Python retrieval core, deployed with a VPS-ready backend path. The system is designed to stay inspectable: retrieval is hybrid, answers are citation-aware, and the supporting passages remain visible instead of being hidden behind a polished summary.

Live workspace: https://app.helpmateai.xyz

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

- On the stabilized `2026-04-19` vendor rerun, Helpmate outperformed both external baselines across all four main document families we track: health policy, thesis, `pancreas7`, and `pancreas8`.
- Averaged across those four families, Helpmate now leads Vectara by `+0.1997` faithfulness, `+0.1350` answer relevancy, and `+0.1523` context precision, and leads OpenAI File Search by `+0.4532`, `+0.4021`, and `+0.3697` on the same `ragas` metrics.
- Current answer-quality snapshot versus Vectara: health policy `0.8846 / 0.6378 / 0.8825` vs `0.7692 / 0.4504 / 0.8235`, thesis `1.0000 / 0.6031 / 0.8588` vs `0.8750 / 0.5579 / 0.8035`, `pancreas7` `0.9444 / 0.6499 / 1.0000` vs `0.6111 / 0.5009 / 0.7350`, and `pancreas8` `0.9250 / 0.5527 / 0.9000` vs `0.7000 / 0.3941 / 0.6700` for `ragas` faithfulness / answer relevancy / context precision.
- Internal ablations still justify the current stack: reranker improved answer-layer supported rate from `0.8026` to `0.8816`, improved citation page-hit rate from `0.6974` to `0.8684`, and planner plus reranker lifted evidence-fragment recall to `0.7364`.
- The evidence selector is now benchmark-validated in reorder-only mode rather than prune mode. In production, the spread-triggered selector keeps strong answer quality (`0.8816` supported-answer rate, `0.9534` focused-`ragas` faithfulness, `0.6501` answer relevancy, `0.9404` context precision) without paying the always-on cost on every query.

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
