-- HelpmateAI online feedback table.
--
-- Apply this in the Supabase SQL editor when using:
--   HELPMATE_STATE_STORE_BACKEND=supabase
--
-- Why:
--   The Production Safety Pack landed cost/observability columns on
--   helpmate_run_traces — model, prompt/completion tokens, USD cost
--   per query. That tells us how much each answer costs us, but says
--   nothing about whether the answer was *useful*. This table closes
--   that loop with a thumb-up / thumb-down signal pinned to a
--   trace_id so dashboards can join feedback × model × cost.
--
-- What:
--   One row per user-issued feedback event.
--
--     feedback_id   uuid           PK, server-generated
--     user_id       uuid           FK to auth.users — cascade so a
--                                  deleted user's feedback is removed
--     trace_id      uuid           nullable — soft FK to
--                                  helpmate_run_traces.trace_id. We
--                                  don't enforce the FK because some
--                                  feedback surfaces (a copy button,
--                                  a citation pill) may produce events
--                                  outside the trace lifecycle.
--     surface       text           where the feedback was issued.
--                                  Defaults to 'answer'; future
--                                  values include 'citation',
--                                  'starter_question', etc.
--     rating        text           'up' or 'down' (CHECK constraint)
--     comment       text           free-text follow-up. Always
--                                  defaulted to '' (no nulls in this
--                                  column) so the analytics pipeline
--                                  doesn't have to coalesce.
--     created_at    timestamptz    server-side write time
--
-- RLS:
--   Users can SELECT and INSERT their own rows. UPDATE/DELETE are
--   intentionally NOT granted — feedback is append-only from the
--   client; the service_role can correct rows via the dashboard if
--   needed. This matches the run_traces pattern (writes through
--   service_role bypass RLS).

create table if not exists public.helpmate_feedback (
    feedback_id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users (id) on delete cascade,
    trace_id uuid,
    surface text not null default 'answer',
    rating text not null check (rating in ('up', 'down')),
    comment text not null default '',
    created_at timestamptz not null default timezone('utc', now())
);

-- Two indexes for the dashboards we plan to power:
--   1) `helpmate_feedback_trace_id_idx` — join to helpmate_run_traces
--      for the model × cost × rating rollups (the high-value query
--      for the LinkedIn-style observability dashboard).
--   2) `helpmate_feedback_user_created_idx` — per-user history /
--      time-windowed counts. Composite on (user_id, created_at desc)
--      so "show me my last 50 ratings" is an index-only scan.
create index if not exists helpmate_feedback_trace_id_idx
    on public.helpmate_feedback (trace_id);

create index if not exists helpmate_feedback_user_created_idx
    on public.helpmate_feedback (user_id, created_at desc);

-- RLS: rows are visible only to their owner; service_role bypasses
-- RLS (so the FastAPI service writing through the service key can
-- read aggregations across users).
alter table public.helpmate_feedback enable row level security;

drop policy if exists "users can read own feedback"
on public.helpmate_feedback;
create policy "users can read own feedback"
on public.helpmate_feedback
for select
to authenticated
using (auth.uid() = user_id);

drop policy if exists "users can insert own feedback"
on public.helpmate_feedback;
create policy "users can insert own feedback"
on public.helpmate_feedback
for insert
to authenticated
with check (auth.uid() = user_id);

-- Deliberately NO update / delete policies — feedback is
-- append-only. Service_role still bypasses RLS for admin tooling.
