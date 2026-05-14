-- Helpmate monthly quota counters table + atomic increment RPCs.
--
-- Apply this in the Supabase SQL editor when using:
--   HELPMATE_STATE_STORE_BACKEND=supabase
--
-- Step 3 of the tier-enforcement series. Provides:
--   • helpmate_quota_counters table — one row per (user, calendar month UTC)
--   • increment_question_counter / increment_premium_counter RPC functions
--     that atomically UPSERT and RETURN the new value
--   • RLS policies so users can only read their own counters
--   • monthly partition is implicit: period_start is the first-of-month date
--     in UTC, derived in the RPC. No partitioning DDL required at our scale;
--     ~1 row per user per month is tiny.
--
-- The premium column is populated by Step 5; Step 3 only wires the question
-- counter. The column is defined here from day one so the migration runs once.

create table if not exists public.helpmate_quota_counters (
    user_id uuid not null references auth.users (id) on delete cascade,
    period_start date not null,
    questions integer not null default 0,
    premium integer not null default 0,
    created_at timestamptz not null default timezone('utc', now()),
    updated_at timestamptz not null default timezone('utc', now()),
    primary key (user_id, period_start)
);

create index if not exists helpmate_quota_counters_user_id_idx
on public.helpmate_quota_counters (user_id);

-- RLS: a user can read their own counter rows. Inserts + updates go through
-- the RPC functions below (security definer) so they don't need direct
-- INSERT/UPDATE policies. Service role still has full access for ops.
alter table public.helpmate_quota_counters enable row level security;

drop policy if exists "users can read own quota counters" on public.helpmate_quota_counters;
create policy "users can read own quota counters"
on public.helpmate_quota_counters
for select
to authenticated
using (auth.uid() = user_id);

-- ---------------------------------------------------------------------------
-- Atomic increment RPCs.
--
-- Pattern: INSERT ... ON CONFLICT ... DO UPDATE ... RETURNING. PostgreSQL
-- guarantees the upsert is atomic, so two concurrent /qa requests from the
-- same user produce two distinct return values (N+1 and N+2) without race.
--
-- The current period_start is computed inside the function via
-- date_trunc('month', timezone('utc', now()))::date. Callers don't need
-- to pass it — keeps the RPC interface narrow.
-- ---------------------------------------------------------------------------

create or replace function public.increment_question_counter(p_user_id uuid)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    new_count integer;
begin
    insert into public.helpmate_quota_counters (user_id, period_start, questions)
    values (
        p_user_id,
        date_trunc('month', timezone('utc', now()))::date,
        1
    )
    on conflict (user_id, period_start)
    do update set
        questions = helpmate_quota_counters.questions + 1,
        updated_at = timezone('utc', now())
    returning questions into new_count;
    return new_count;
end;
$$;

create or replace function public.increment_premium_counter(p_user_id uuid)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    new_count integer;
begin
    insert into public.helpmate_quota_counters (user_id, period_start, premium)
    values (
        p_user_id,
        date_trunc('month', timezone('utc', now()))::date,
        1
    )
    on conflict (user_id, period_start)
    do update set
        premium = helpmate_quota_counters.premium + 1,
        updated_at = timezone('utc', now())
    returning premium into new_count;
    return new_count;
end;
$$;

-- Restrict execution to service_role. The RPC takes user_id as a
-- parameter (not auth.uid()), so granting EXECUTE to `authenticated`
-- would let any signed-in user call:
--     supabase.rpc('increment_question_counter', {p_user_id: '<victim>'})
-- and burn another user's quota. The backend uses the service_role
-- key (HELPMATE_SUPABASE_SERVICE_ROLE_KEY) so it can call these
-- functions while client-side calls cannot.
revoke all on function public.increment_question_counter(uuid) from public;
revoke all on function public.increment_question_counter(uuid) from authenticated;
revoke all on function public.increment_premium_counter(uuid) from public;
revoke all on function public.increment_premium_counter(uuid) from authenticated;
grant execute on function public.increment_question_counter(uuid) to service_role;
grant execute on function public.increment_premium_counter(uuid) to service_role;
