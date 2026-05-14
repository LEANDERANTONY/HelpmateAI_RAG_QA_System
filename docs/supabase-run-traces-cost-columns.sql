-- Adds per-query cost-tracking columns to ``helpmate_run_traces``.
--
-- Apply this in the Supabase SQL editor when using:
--   HELPMATE_STATE_STORE_BACKEND=supabase
--
-- Why:
--   The existing trace row records the question, retrieval plan, and
--   answer support status, but says nothing about how many tokens we
--   spent producing it. Without that signal we can't validate the
--   tier-margin math from the pricing rollout: a tier whose median
--   query costs $0.0035 with a $0.40 retail price has a margin band
--   we want to monitor over time, not a thing we guess at.
--
-- What:
--   Three new columns + a model-name column so dashboards can group
--   by it without parsing the JSON payload:
--
--     prompt_tokens      int           total prompt tokens across all
--                                      LLM calls in the query
--     completion_tokens  int           total completion tokens
--     cost_usd           numeric(10,6) computed USD cost (six-decimal
--                                      precision so per-query
--                                      sub-cent costs round cleanly)
--     model_name         text          the primary (highest-cost)
--                                      model used in the query —
--                                      mirrors what answer.model_name
--                                      stores, just hoisted into its
--                                      own column for filtering
--
-- The Python write path lives in ``src/traces/store.py`` (the
-- ``SupabaseRunTraceStore`` class). The matching pull from the
-- per-call ``LLMCallRecord`` aggregation happens in
-- ``backend/openai_service.py::CostCollector.totals()``.
--
-- Defaults:
--   All columns default to 0 / "" so the existing rows backfill
--   cleanly. Existing reads continue to work — the trace store reads
--   the new columns through ``.get(...)`` so a missing value in an
--   old row reads as the default.

alter table public.helpmate_run_traces
    add column if not exists prompt_tokens int not null default 0,
    add column if not exists completion_tokens int not null default 0,
    add column if not exists cost_usd numeric(10, 6) not null default 0,
    add column if not exists model_name text not null default '';

-- Index on cost_usd so the cost-margin dashboard can pull the
-- p50/p95/p99 of cost per tier without a full table scan.
create index if not exists helpmate_run_traces_cost_usd_idx
on public.helpmate_run_traces (cost_usd);

-- Index on model_name for cost-per-model rollups. Cardinality stays
-- low (under a dozen distinct values across the pricing table), so
-- the index is small.
create index if not exists helpmate_run_traces_model_name_idx
on public.helpmate_run_traces (model_name);

-- Note on RLS:
--   The cost columns inherit the existing read/write policies from
--   ``helpmate_run_traces`` (see ``docs/supabase-workspace-retention.sql``).
--   We deliberately do NOT add a separate cost-only policy; the
--   trace row is already user-scoped, and there's no use case for a
--   "users can see cost but not other trace fields" split.
