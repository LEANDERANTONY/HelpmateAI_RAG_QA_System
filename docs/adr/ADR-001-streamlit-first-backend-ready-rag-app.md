# ADR-001: Streamlit-First, Backend-Ready RAG App

- Status: Accepted
- Date: 2026-04-01

## Context

The repository started as a notebook-first RAG prototype. The next phase needed a real app surface, deployability, modular code organization, and room for a later backend extraction without taking on unnecessary complexity upfront.

## Decision

Adopt a Streamlit-first application shape with transport-agnostic core services under `src/`, using a thin `app.py` entrypoint and clean service boundaries so FastAPI can be added later if justified.

## Consequences

Positive:

- faster path from notebook prototype to usable app
- simpler deployment story for Docker and Render-style hosting
- easier UI iteration during early product shaping
- backend extraction remains possible because ingestion, retrieval, generation, and caching are not embedded in UI code

Tradeoffs:

- Streamlit session flow is less flexible than a dedicated API backend for long-running jobs
- concurrency and background-task control remain limited until a backend is extracted
- some UI state concerns still need careful discipline to avoid leaking into service logic

## Notes

FastAPI remains a planned later step, not a rejected option. The threshold for extraction is operational need, not architectural fashion.
