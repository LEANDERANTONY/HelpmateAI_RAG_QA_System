# Implementation History And Challenges

This document summarizes the important engineering changes made so far, the problems they exposed, and the improvement each change unlocked.

## 1. From Notebook Demo To App Architecture

Change:

- moved from a notebook-driven prototype to a Streamlit app with modular `src/` services

Challenge:

- the notebook mixed experimentation, ingestion, retrieval, and answer generation in one place
- there was no clear deployment path and no clean surface for testing

Improvement:

- separated UI, pipeline, retrieval, generation, and caching concerns
- made the project deployable and easier to evolve safely

## 2. Local-First Persistent RAG

Change:

- added Chroma-based persistence, reusable indexes, and typed chunk records

Challenge:

- repeated testing was slow without index reuse
- retrieval behavior was hard to inspect when everything lived in notebook cells

Improvement:

- persisted indexes made experimentation practical
- saved chunk metadata made retrieval debugging easier

## 3. Hybrid Retrieval Instead Of Embeddings-Only

Change:

- combined dense retrieval with TF-IDF lexical retrieval and fusion

Challenge:

- policy documents contain exact clauses, definitions, and phrasing that pure vector retrieval can miss

Improvement:

- better exact-term recall
- better handling of clause-like questions and domain-specific wording

## 4. Reranking, Query Rewriting, And Adaptive Retry

Change:

- added reranking, query rewriting fallback, and weak-evidence re-retrieval

Challenge:

- first-pass retrieval was not always good enough for grounded answers
- users need strong retrieval before answer generation matters

Improvement:

- more resilient retrieval pipeline
- clearer strategy notes and retrieval transparency

## 5. Structured Abstention

Change:

- introduced explicit `supported` output in answer generation

Challenge:

- unsupported questions could still produce vague or overconfident text

Improvement:

- unsupported answers now fail more honestly
- negative evals became more meaningful

## 6. Benchmark-Driven Quality Control

Change:

- added positive evals, negative evals, OpenAI file-search comparison, and saved reports

Challenge:

- without evals, it was too easy to mistake anecdotal success for real retrieval quality
- some early misses turned out to be bad benchmark labels rather than retrieval failures

Improvement:

- quality claims became measurable
- the team could compare local RAG against a hosted baseline on the same document

## 7. Metadata-Aware Retrieval

Change:

- added page-aware filtering, section-heading signals, and richer citations

Challenge:

- many queries refer to pages, sections, or exact clause neighborhoods

Improvement:

- retrieval became more controllable and easier to interpret

## 8. Document-Intelligence Layer

Change:

- added structure inference and query analysis before ranking

Challenge:

- document-specific tuning risked overfitting
- improvements that helped one policy PDF would not necessarily generalize to a thesis or report

Improvement:

- chunk metadata now carries section paths, clause ids, and content types
- retrieval can softly prefer definition, waiting-period, process, or benefit-style evidence depending on the question

## 9. Chroma Portability Issues

Change:

- sanitized metadata written to Chroma while preserving rich local chunk metadata

Challenge:

- Chroma only accepts scalar metadata values
- the new structure-aware layer produced lists such as `section_path` and `clause_ids`

Improvement:

- structured metadata now works with the local store and Chroma index persistence
- the richer retrieval layer remains compatible with the storage backend

## 10. Cross-Document Generalization Findings

What worked well:

- policy wording documents
- exact factual questions
- clause-adjacent operational queries
- negative abstention behavior

What became harder:

- thesis and research-style narrative content
- synthesis across broader sections
- future-work and interpretive questions
- result-summary queries that are semantically distributed rather than concentrated in one clause block

## What This Means For The Next Step

The architecture is now strong enough that the next improvement should not be another repo restructure.

The most justified next steps are:

- section-first or clause-first retrieval
- section-summary embeddings
- stronger query understanding for academic and report-style documents
- harder benchmark sets spanning multiple document families
