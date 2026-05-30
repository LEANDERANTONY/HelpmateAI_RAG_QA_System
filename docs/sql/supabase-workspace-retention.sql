-- Helpmate workspace-retention hardening for Supabase-backed state.
--
-- Apply this in the Supabase SQL editor when using:
--   HELPMATE_STATE_STORE_BACKEND=supabase
--
-- This keeps the current app-driven retention behavior, but adds:
-- - explicit user_id / last_activity_at / expires_at columns on helpmate_documents
-- - database-side expiry cleanup through pg_cron
-- - RLS policies so future direct client reads stay user-scoped
-- - cascading delete from documents -> indexes -> artifacts -> run traces

create extension if not exists pg_cron with schema extensions;

alter table public.helpmate_documents
    add column if not exists user_id uuid references auth.users (id) on delete cascade,
    add column if not exists last_activity_at timestamptz,
    add column if not exists expires_at timestamptz;

update public.helpmate_documents
set
    user_id = case
        when coalesce(payload #>> '{metadata,_workspace_owner_user_id}', '') ~* '^[0-9a-f-]{36}$'
            then (payload #>> '{metadata,_workspace_owner_user_id}')::uuid
        else user_id
    end,
    last_activity_at = coalesce(
        last_activity_at,
        case
            when coalesce(payload #>> '{metadata,_workspace_last_activity_at}', '') <> ''
                then (payload #>> '{metadata,_workspace_last_activity_at}')::timestamptz
            else null
        end
    ),
    expires_at = coalesce(
        expires_at,
        case
            when coalesce(payload #>> '{metadata,_workspace_expires_at}', '') <> ''
                then (payload #>> '{metadata,_workspace_expires_at}')::timestamptz
            else null
        end
    )
where
    user_id is null
    or last_activity_at is null
    or expires_at is null;

create index if not exists helpmate_documents_user_id_idx
on public.helpmate_documents (user_id);

create index if not exists helpmate_documents_expires_at_idx
on public.helpmate_documents (expires_at);

create table if not exists public.helpmate_run_traces (
    trace_id text primary key,
    document_id text not null,
    fingerprint text not null,
    question text not null,
    payload jsonb not null,
    created_at timestamptz not null default timezone('utc', now()),
    expires_at timestamptz not null
);

create index if not exists helpmate_run_traces_document_id_idx
on public.helpmate_run_traces (document_id);

create index if not exists helpmate_run_traces_expires_at_idx
on public.helpmate_run_traces (expires_at);

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'helpmate_indexes_document_id_fkey'
    ) then
        alter table public.helpmate_indexes
            add constraint helpmate_indexes_document_id_fkey
            foreign key (document_id)
            references public.helpmate_documents (document_id)
            on delete cascade;
    end if;
end;
$$;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'helpmate_index_artifacts_document_id_fkey'
    ) then
        alter table public.helpmate_index_artifacts
            add constraint helpmate_index_artifacts_document_id_fkey
            foreign key (document_id)
            references public.helpmate_documents (document_id)
            on delete cascade;
    end if;
end;
$$;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'helpmate_run_traces_document_id_fkey'
    ) then
        alter table public.helpmate_run_traces
            add constraint helpmate_run_traces_document_id_fkey
            foreign key (document_id)
            references public.helpmate_documents (document_id)
            on delete cascade;
    end if;
end;
$$;

alter table public.helpmate_documents enable row level security;
alter table public.helpmate_indexes enable row level security;
alter table public.helpmate_index_artifacts enable row level security;
alter table public.helpmate_run_traces enable row level security;

drop policy if exists "users can read own helpmate documents" on public.helpmate_documents;
create policy "users can read own helpmate documents"
on public.helpmate_documents
for select
to authenticated
using (
    auth.uid() = user_id
    -- expires_at IS NULL means the workspace has no retention window
    -- (Business tier keeps documents indefinitely); such rows must stay
    -- visible. Only a SET, PAST expiry hides a row (L5).
    and (
        expires_at is null
        or expires_at > timezone('utc', now())
    )
);

drop policy if exists "users can insert own helpmate documents" on public.helpmate_documents;
create policy "users can insert own helpmate documents"
on public.helpmate_documents
for insert
to authenticated
with check (auth.uid() = user_id);

drop policy if exists "users can update own helpmate documents" on public.helpmate_documents;
create policy "users can update own helpmate documents"
on public.helpmate_documents
for update
to authenticated
using (auth.uid() = user_id)
with check (auth.uid() = user_id);

drop policy if exists "users can delete own helpmate documents" on public.helpmate_documents;
create policy "users can delete own helpmate documents"
on public.helpmate_documents
for delete
to authenticated
using (auth.uid() = user_id);

drop policy if exists "users can read own helpmate indexes" on public.helpmate_indexes;
create policy "users can read own helpmate indexes"
on public.helpmate_indexes
for select
to authenticated
using (
    exists (
        select 1
        from public.helpmate_documents d
        where d.document_id = helpmate_indexes.document_id
          and d.user_id = auth.uid()
          -- NULL d.expires_at = no retention window (Business tier);
          -- keep visible. Only a set, past expiry hides the row (L5).
          and (
              d.expires_at is null
              or d.expires_at > timezone('utc', now())
          )
    )
);

drop policy if exists "users can write own helpmate indexes" on public.helpmate_indexes;
create policy "users can write own helpmate indexes"
on public.helpmate_indexes
for all
to authenticated
using (
    exists (
        select 1
        from public.helpmate_documents d
        where d.document_id = helpmate_indexes.document_id
          and d.user_id = auth.uid()
    )
)
with check (
    exists (
        select 1
        from public.helpmate_documents d
        where d.document_id = helpmate_indexes.document_id
          and d.user_id = auth.uid()
    )
);

drop policy if exists "users can read own helpmate artifacts" on public.helpmate_index_artifacts;
create policy "users can read own helpmate artifacts"
on public.helpmate_index_artifacts
for select
to authenticated
using (
    exists (
        select 1
        from public.helpmate_documents d
        where d.document_id = helpmate_index_artifacts.document_id
          and d.user_id = auth.uid()
          -- NULL d.expires_at = no retention window (Business tier);
          -- keep visible. Only a set, past expiry hides the row (L5).
          and (
              d.expires_at is null
              or d.expires_at > timezone('utc', now())
          )
    )
);

drop policy if exists "users can write own helpmate artifacts" on public.helpmate_index_artifacts;
create policy "users can write own helpmate artifacts"
on public.helpmate_index_artifacts
for all
to authenticated
using (
    exists (
        select 1
        from public.helpmate_documents d
        where d.document_id = helpmate_index_artifacts.document_id
          and d.user_id = auth.uid()
    )
)
with check (
    exists (
        select 1
        from public.helpmate_documents d
        where d.document_id = helpmate_index_artifacts.document_id
          and d.user_id = auth.uid()
    )
);

drop policy if exists "users can read own helpmate run traces" on public.helpmate_run_traces;
create policy "users can read own helpmate run traces"
on public.helpmate_run_traces
for select
to authenticated
using (
    exists (
        select 1
        from public.helpmate_documents d
        where d.document_id = helpmate_run_traces.document_id
          and d.user_id = auth.uid()
          -- NULL d.expires_at = no retention window (Business tier);
          -- keep visible. Only a set, past expiry hides the row (L5).
          and (
              d.expires_at is null
              or d.expires_at > timezone('utc', now())
          )
    )
);

drop policy if exists "users can write own helpmate run traces" on public.helpmate_run_traces;
create policy "users can write own helpmate run traces"
on public.helpmate_run_traces
for all
to authenticated
using (
    exists (
        select 1
        from public.helpmate_documents d
        where d.document_id = helpmate_run_traces.document_id
          and d.user_id = auth.uid()
    )
)
with check (
    exists (
        select 1
        from public.helpmate_documents d
        where d.document_id = helpmate_run_traces.document_id
          and d.user_id = auth.uid()
    )
);

-- Note: the original version of this file shipped a SQL-only sweeper
-- (cleanup_expired_helpmate_workspaces) plus a pg_cron job that ran it
-- every 5 minutes. Both were removed in May 2026 (Supabase migration
-- `drop_legacy_cleanup_rpc`) when Step 6 of the tier-enforcement series
-- shipped a Python-based replacement in backend/maintenance.py.
--
-- The new sweeper:
--   1. Does everything the SQL RPC did (DELETE expired rows in
--      helpmate_documents + helpmate_run_traces)
--   2. Also deletes the bucket objects in Supabase Storage (pure SQL
--      cannot reach across to the Storage API — bucket objects would
--      orphan under the SQL-only design)
--   3. Is tier-aware (Free 30d / Pro 365d / Business unbounded) instead
--      of the hardcoded 24h window the RPC inherited from
--      _touch_document_workspace
--   4. Cleans orphan upload files / index dirs / cache files on the VPS
--
-- The Python sweeper is scheduled via VPS crontab (every 10 minutes):
--     */10 * * * * docker exec helpmate-api python -m backend.maintenance
--
-- If you ever need the SQL fallback again (e.g. a fresh project that
-- hasn't deployed the Python container yet), the original definitions
-- are preserved in git history of this file before commit 9a1028e.
