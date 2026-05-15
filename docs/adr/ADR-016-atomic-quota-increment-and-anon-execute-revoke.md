# ADR-016: Atomic Quota Increment Via SECURITY DEFINER RPC

Date: 2026-05-15

Status: Shipped

## Context

The monthly question quota at `/qa` needs to be tamper-proof and race-free under realistic concurrent load:

- A user cannot be allowed to burn other users' quotas by passing a victim's UUID into a write call. The /qa handler runs as the backend, which uses the Supabase service-role key — but the same RPC functions are reachable from any Supabase client unless explicitly revoked
- Two concurrent /qa requests from the same user at cap-1 must not both produce successful answers (N+1 on a cap of N). A read-then-write pattern in Python is non-atomic across the round-trip and races on real traffic
- Failed pipeline runs must not burn the user's quota. The user retries on every error message anyway, and pre-incrementing means a transient OpenAI 503 ends with the counter one short of the user's actual usage
- The counter table is the only piece of paid-tier state on day one (the subscriptions table arrives on Day 33). The pattern picked here had to survive being the only durable enforcement boundary

The brief considered two patterns: "atomic pre-increment + rollback on failure" and "pre-check + post-increment". The trade-off is a small race window in the second pattern against the operational complexity of the first.

## Decision

Implement the counter as a Postgres table plus a pair of `SECURITY DEFINER` RPC functions, increment AFTER successful pipeline completion, and lock the RPC down to `service_role` only.

### Table + RPC

`public.helpmate_quota_counters` has one row per `(user_id, period_start)` where `period_start` is the first-of-month UTC date. The RPC functions:

```sql
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
```

Postgres `INSERT ... ON CONFLICT ... DO UPDATE ... RETURNING` is atomic, so two concurrent calls deterministically return N+1 and N+2 — never two N+1 values, never a missed increment. A symmetric `increment_premium_counter(p_user_id)` exists for premium-answer accounting.

### Gate order

The `/qa` handler runs:

1. (If premium=true) Check premium availability → check premium quota → block on 402
2. Check standard question quota → block on 402
3. Run the pipeline
4. On success, call `increment_question_counter(user_id)`
5. (If premium=true on success) Also call `increment_premium_counter(user_id)`

Pipeline failure → no increment. The user retries; no rollback path is needed because no increment was attempted.

The trade-off: a small race window exists where two concurrent requests at cap-1 can both pre-check pass, both run the pipeline, and both increment, ending at N+1. The third call then rejects. We accepted this because the alternative ("atomic pre-increment + rollback on failure") is meaningfully more complex (the failure path has to undo the increment in a separate transaction, and a crash between the failure and the rollback leaves the counter permanently inflated) for negligible real-world benefit at our scale.

### EXECUTE permissions

The RPC takes `p_user_id` as a parameter, not `auth.uid()`. That means any role with EXECUTE could burn another user's quota by passing a victim's UUID:

```js
// would-be attack if anyone but service_role can call this:
supabase.rpc('increment_question_counter', { p_user_id: '<victim uuid>' })
```

The intended permission model is: `service_role` only. The backend uses `HELPMATE_SUPABASE_SERVICE_ROLE_KEY` so backend-driven increments work; client-side calls (with any anon or authenticated key) fail with `permission denied for function`.

### The anon EXECUTE gap

The first iteration of the migration revoked EXECUTE from `public` and `authenticated`. Caught post-merge: **Supabase grants `anon` EXECUTE on public-schema functions by default**, and `anon` is the role under the public anon key shipped to every browser client. Without a revoke against `anon`, an unauthenticated caller could have invoked `increment_question_counter` against any user's UUID via the public anon key — the exact attack the RPC parameterization was designed to make explicit.

Closed in commit `9a1028e` via Supabase migration `revoke_anon_quota_rpcs` (applied `20260514154130`) and backported into `docs/sql/supabase-quota-counters.sql` so a fresh-DB redeploy is secure out of the box. The final revoke set is all three of `public`, `authenticated`, and `anon`; only `service_role` retains EXECUTE.

## Consequences

The counter is atomic and tamper-proof at the database boundary. The Python layer cannot accidentally drop an increment (it never tries to mutate the value directly), and no client — anonymous, authenticated, or impersonating another user — can write to it.

The "increment on success" pattern means user-visible counters are a true reflection of pipeline runs. A pipeline failure (OpenAI 503, Supabase outage, malformed evidence) doesn't tick the counter, so the retry doesn't cost a question. This matches the user's mental model: "I asked a question, got an answer, that's one usage" — not "I tried to ask a question, the server choked, and now I'm down a question for the month".

RLS on the table is read-only for authenticated users on their own rows. The frontend reads counters via `/workspace/quota` (a backend endpoint that does its own service-role read), not via direct Supabase queries, so the RLS policy mostly exists for defense-in-depth and ad-hoc inspection through the Supabase studio.

The race window at cap-1 is real but bounded. Worst case: a user at 499/500 fires two concurrent `/qa` requests; both pre-check pass; both increment; counter lands at 501/500. The third request rejects. The user got one "free" question over their cap. We are okay with this — the alternative is operational complexity (rollback paths, crash recovery) that buys negligible enforcement at our scale.

The anon EXECUTE hotfix is a real lesson that surfaced in the security review during the rollout, not in dev (dev has no anonymous Supabase traffic) and not in any test we had at the time. The fix landed within hours of detection, but the underlying class of mistake — Supabase's default permissions are more permissive than most developers expect — is worth keeping front-of-mind for any future RPC that takes a user-id parameter. The migration file now includes all three revokes explicitly with an inline comment explaining why.

## Validation

Unit tests in `tests/backend/test_supabase_quota_store.py` verify:

- `record_question_attempt` returns the value from `increment_question_counter`
- `record_premium_attempt` returns the value from `increment_premium_counter`
- An RPC-permission error is surfaced as a `QuotaStoreError`, not swallowed silently

Integration tests in `tests/backend/test_api_quota.py` verify:

- `/qa` returns 402 with `code="question_quota_exhausted"` once `current >= limit`
- A premium request from a Free user returns 402 with `code="premium_unavailable"`
- A premium request from a Pro user at premium cap returns 402 with `code="premium_quota_exhausted"`
- A failed pipeline does NOT increment the counter
- A successful pipeline increments standard once; a successful premium pipeline increments BOTH standard and premium once

Manual security check executed against the production Supabase project after the anon revoke landed:

- An unauthenticated request to `rpc('increment_question_counter', { p_user_id: '<test uuid>' })` returns `permission denied for function increment_question_counter`
- An authenticated request (using a real user's anon-client token) returns the same `permission denied`
- The backend's service-role-keyed call continues to return the new counter value

The `docs/sql/supabase-quota-counters.sql` file in the repo is the authoritative migration; the production database matches it.
