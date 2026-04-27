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
    and expires_at is not null
    and expires_at > timezone('utc', now())
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
          and d.expires_at is not null
          and d.expires_at > timezone('utc', now())
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
          and d.expires_at is not null
          and d.expires_at > timezone('utc', now())
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
          and d.expires_at is not null
          and d.expires_at > timezone('utc', now())
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

create or replace function public.cleanup_expired_helpmate_workspaces()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    deleted_count integer := 0;
begin
    delete from public.helpmate_run_traces
    where expires_at is not null
      and expires_at <= timezone('utc', now());

    delete from public.helpmate_documents
    where expires_at is not null
      and expires_at <= timezone('utc', now());

    get diagnostics deleted_count = row_count;
    return deleted_count;
end;
$$;

revoke all on function public.cleanup_expired_helpmate_workspaces() from public;
grant execute on function public.cleanup_expired_helpmate_workspaces() to service_role;

select public.cleanup_expired_helpmate_workspaces();

do $$
begin
    if exists (
        select 1
        from cron.job
        where jobname = 'cleanup-expired-helpmate-workspaces'
    ) then
        perform cron.unschedule('cleanup-expired-helpmate-workspaces');
    end if;
exception
    when undefined_table then
        null;
end;
$$;

select cron.schedule(
    'cleanup-expired-helpmate-workspaces',
    '*/5 * * * *',
    $$select public.cleanup_expired_helpmate_workspaces();$$
);
