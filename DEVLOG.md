# Devlog

## 2026-04-01

- Refactored the repo from a notebook-first layout into an app-oriented structure
- Added `pyproject.toml`, Streamlit entrypoint, Dockerfile, Render manifest, and docs scaffolding
- Standardized dependency management on `uv` with `pyproject.toml` and `uv.lock` as the canonical setup
- Implemented PDF/DOCX ingestion, deterministic chunking, Chroma-backed index persistence, hybrid retrieval, reranking hook, and answer cache
- Added a themed Streamlit UI aligned with the visual language of the AI Job Application Agent project
- Kept the original notebook as a reference artifact instead of the primary implementation surface
