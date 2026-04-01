# Architecture

HelpmateAI is a Streamlit-first long-document QA app with transport-agnostic core services.

## Runtime Shape

- `app.py` is a thin Streamlit launcher.
- `src/ui/` owns theming and page flow.
- `src/pipeline/` coordinates ingestion, index build/reuse, retrieval, and answer generation.
- The implementation is backend-ready, but phase 1 intentionally does not add FastAPI.

## Retrieval Stack

- Ingest PDF and DOCX documents into normalized text plus metadata
- Chunk deterministically with overlap
- Persist the dense index in Chroma keyed by document fingerprint
- Run hybrid retrieval:
  - dense retrieval from Chroma
  - lexical retrieval via TF-IDF scoring
  - reciprocal-rank style fusion
  - optional cross-encoder reranking
- Apply metadata-aware page filtering when the question references specific pages
- Retry weak retrievals with rewritten query variants before generating an answer
- Generate grounded answers from the final evidence set

## Caching

- Index cache: reuse persisted indexes for unchanged documents
- Answer cache: reuse safe answer results when document fingerprint, normalized question, retrieval version, and generation version still match

## Future Expansion

- Supabase for auth, usage, and saved workspaces
- FastAPI extraction only when background jobs or multi-user hosting justify it
- separate paraphrasing app as a sibling product that can share some document-processing patterns
