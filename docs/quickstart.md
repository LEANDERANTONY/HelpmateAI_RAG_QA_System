# Quickstart

## Local Run

1. Install `uv`.
2. From the repo root, run `uv sync`.
3. Export `OPENAI_API_KEY` if you want live model-backed answer generation.
4. Start the app with `streamlit run app.py`.

## First Workflow

1. Upload a PDF or DOCX file.
2. Click `Build Or Reuse Index`.
3. Ask a grounded question.
4. Review the answer, citations, and retrieved evidence.

## Notes

- The app persists indexes under `data/indexes/`.
- Cached answers live under `data/cache/`.
- If `OPENAI_API_KEY` is not set, the app falls back to a local grounded-summary response.
