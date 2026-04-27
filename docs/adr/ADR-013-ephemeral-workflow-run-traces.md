# ADR-013: Ephemeral Workflow Run Traces

Date: 2026-04-27

Status: Experimental branch

## Context

The retrieval workflow now includes several model-assisted decisions: orchestration, reranking, evidence selection, and answer generation. Each model call receives explicit context, but there was no first-class record of what happened across the full question-answer run.

External memory systems can help with user preferences or long-lived project memory, but grounded document QA needs something narrower first: a temporary trace of one workflow run that can explain why a specific answer used specific evidence.

## Decision

Add `RunTraceRecord` as an ephemeral workflow trace. A trace records:

- document and question identifiers
- retrieval route, plan, filters, scores, and strategy notes
- candidate IDs, page/section/chapter metadata, scores, and short previews
- answer support status, citations, citation details, and model name
- `created_at` and `expires_at`

The trace deliberately does not store full document text or the full answer body. Candidate text is limited to short previews.

Run traces are persisted through a local/Supabase trace store:

- local traces live under `data/api_state/run_traces`
- Supabase traces live in `helpmate_run_traces`
- traces expire using the same workspace retention model as uploads and indexes
- deleting a workspace deletes its traces
- the local sweeper deletes expired trace records

## Consequences

This creates workflow memory for debugging and evaluation without turning the product into a long-term memory system. Traces can support later tooling such as run inspectors, failed-answer clustering, and feedback-driven evals.

The Supabase retention SQL now creates and protects `helpmate_run_traces`, applies RLS through the parent document owner, cascades deletes from expired documents, and deletes expired trace rows in the cleanup function.

## Validation

Unit coverage verifies:

- compact trace construction
- fallback expiry from `HELPMATE_WORKSPACE_RETENTION_HOURS`
- local trace expiry deletion
- Supabase trace persistence/deletion calls
- workspace sweeper cleanup of expired traces

`run_trace_eval` verifies:

- trace is saved
- expiry matches workspace expiry
- candidate previews are limited
- full document text is not copied
- full answer body is not copied
- retrieval plan context is present

Full test suite result on this branch after the front-matter scope fix: `116 passed`.
