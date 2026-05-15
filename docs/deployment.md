# Deployment

HelpmateAI deploys as a single Vercel project serving both the marketing landing and the product workspace, plus a `FastAPI` backend on a Docker VPS:

- `Next.js` on Vercel for the landing (`helpmateai.xyz`) and the workspace (`app.helpmateai.xyz`)
- `FastAPI` on a Linux VPS behind Caddy for uploads, indexing, QA, citations, evidence, and the source-file endpoint

Current production shape:

- `helpmateai.xyz` -> Vercel (Next.js apex; serves `/landing/*` via a host-based rewrite in `next.config.ts`)
- `app.helpmateai.xyz` -> Vercel (Next.js workspace; same project)
- `api.helpmateai.xyz` -> VPS (Caddy reverse-proxies TLS to the API container on port `8001`)

The live stack:

- Cloudflare manages DNS and TLS in front of the apex and `api` subdomain
- Vercel serves the unified `Next.js` build; the same deployment routes apex requests to the landing and `app.*` requests to the workspace
- a Linux VPS runs the `FastAPI` backend container plus LibreOffice for DOCX → PDF rendition at ingest

## Recommended Hosting

- Vercel for [frontend](C:\Users\Leander Antony A\Documents\Projects\HelpmateAI_RAG_QA_System\frontend) (single project handles both the apex and the `app` subdomain via host-based rewrites)
- a Linux VPS for the FastAPI backend container

The repo includes:

- [Dockerfile](C:\Users\Leander Antony A\Documents\Projects\HelpmateAI_RAG_QA_System\Dockerfile) for backend deployment, which installs `libreoffice-core` and `libreoffice-writer` for the DOCX rendition path
- [deploy/vps/docker-compose.yml](C:\Users\Leander Antony A\Documents\Projects\HelpmateAI_RAG_QA_System\deploy\vps\docker-compose.yml) for the standard VPS deployment
- [deploy/vps/Caddyfile](C:\Users\Leander Antony A\Documents\Projects\HelpmateAI_RAG_QA_System\deploy\vps\Caddyfile) for TLS and reverse proxying on a VPS
- [.github/workflows/deploy.yml](C:\Users\Leander Antony A\Documents\Projects\HelpmateAI_RAG_QA_System\.github\workflows\deploy.yml) builds the backend image on every `main` push and SSHes to the VPS to pull + recreate the container

## Deployment Flow

1. Deploy the FastAPI backend first. The build action pushes to GHCR and the VPS pulls + recreates via `docker compose`.
2. Confirm the backend health endpoint returns `ok` and that `supported_upload_types` includes `.docx` (the LibreOffice install is required for DOCX rendition).
3. Push to `main`. Vercel auto-deploys the Next.js build to the production target; the GH Actions workflow handles the VPS deploy.
4. In the Vercel project, add `helpmateai.xyz` (apex) and `app.helpmateai.xyz` to the production domain list, plus the matching DNS records at the registrar (apex `A` record at Vercel's anycast IP; `CNAME` for `www` and `app`).
5. The same Next.js build serves both surfaces. The host rewrite in `next.config.ts` runs `beforeFiles` so the apex `/` is rewritten to `/landing` before page routing fires; `app.helpmateai.xyz/` continues to match `app/page.tsx` (the workspace).
6. For larger browser uploads, set `NEXT_PUBLIC_UPLOAD_API_BASE_URL=https://api.yourdomain.com` so file uploads bypass the Vercel body-size limit.
7. Set the API URL on Vercel (`API_REWRITE_TARGET=https://api.yourdomain.com`) and confirm the workspace's API client uses the absolute URL in production. The Read Mode PDF viewer also calls the backend directly via `API_BASE_URL` rather than the relative `/api/*` proxy — this matters in production because the Vercel-edge to Cloudflare proxy chain triggers Cloudflare's bot challenge on data-center origins and breaks the PDF stream. Browser-direct calls to `api.helpmateai.xyz` pass through cleanly.
8. Test upload, indexing, QA, citations, evidence, and Read Mode (including DOCX → PDF rendition + source viewer) on the deployed URLs.

## Backend Environment

Important backend environment variables:

- `OPENAI_API_KEY`
- `HELPMATE_DATA_DIR`
- `HELPMATE_UPLOADS_DIR`
- `HELPMATE_INDEXES_DIR`
- `HELPMATE_CACHE_DIR`
- `HELPMATE_CORS_ORIGINS`

Optional cloud-persistence variables:

- `HELPMATE_STATE_STORE_BACKEND`
- `HELPMATE_VECTOR_STORE_BACKEND`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `HELPMATE_SUPABASE_DOCUMENTS_TABLE`
- `HELPMATE_SUPABASE_INDEXES_TABLE`
- `HELPMATE_SUPABASE_ARTIFACTS_TABLE`
- `HELPMATE_CHROMA_HTTP_HOST`
- `HELPMATE_CHROMA_HTTP_PORT`
- `HELPMATE_CHROMA_HTTP_SSL`
- `HELPMATE_CHROMA_HTTP_TENANT`
- `HELPMATE_CHROMA_HTTP_DATABASE`
- `HELPMATE_CHROMA_HTTP_HEADERS`
- `HELPMATE_WORKSPACE_RETENTION_HOURS`
- `HELPMATE_CHROMA_UPSERT_BATCH_SIZE`

Current production-friendly backend mode:

- `HELPMATE_STATE_STORE_BACKEND=supabase`
- `HELPMATE_VECTOR_STORE_BACKEND=local`

Why:

- Supabase stores user-scoped workspace metadata and retention state
- local vector storage on the VPS avoids managed vector-store cost while the product is still early-stage
- the VPS sweeper clears expired uploads, indexes, and answer-cache files so local disk state does not accumulate forever

Recommended production values:

- `HELPMATE_DATA_DIR=/var/data/helpmate`
- `HELPMATE_UPLOADS_DIR=/var/data/helpmate/uploads`
- `HELPMATE_INDEXES_DIR=/var/data/helpmate/indexes`
- `HELPMATE_CACHE_DIR=/var/data/helpmate/cache`
- `HELPMATE_CORS_ORIGINS=https://app.yourdomain.com`

Why these matter:

- uploads and indexes are persisted outside the container filesystem
- caches survive restarts if the host provides persistent storage
- CORS can be tightened to the deployed app origin instead of `*`

## Cloud-Backed Variant

If you want the VPS backend to stay mostly stateless, Helpmate can run in a cloud-backed mode:

- Supabase stores document records, index records, and the chunk/section/synopsis artifact bundle
- hosted Chroma stores the vector collections
- the FastAPI backend becomes mostly stateless

Recommended backend values in that mode:

- `HELPMATE_STATE_STORE_BACKEND=supabase`
- `HELPMATE_VECTOR_STORE_BACKEND=chroma_http`
- `SUPABASE_URL=https://your-project.supabase.co`
- `SUPABASE_SERVICE_ROLE_KEY=...`
- `HELPMATE_SUPABASE_DOCUMENTS_TABLE=helpmate_documents`
- `HELPMATE_SUPABASE_INDEXES_TABLE=helpmate_indexes`
- `HELPMATE_SUPABASE_ARTIFACTS_TABLE=helpmate_index_artifacts`
- `HELPMATE_CHROMA_HTTP_HOST=your-chroma-host`
- `HELPMATE_CHROMA_HTTP_PORT=443`
- `HELPMATE_CHROMA_HTTP_SSL=true`
- `HELPMATE_CHROMA_HTTP_TENANT=default_tenant`
- `HELPMATE_CHROMA_HTTP_DATABASE=default_database`
- `HELPMATE_WORKSPACE_RETENTION_HOURS=24`
- `HELPMATE_CHROMA_UPSERT_BATCH_SIZE=250`

If the hosted Chroma endpoint requires headers, use:

- `HELPMATE_CHROMA_HTTP_HEADERS=Authorization=Bearer your_token`

The Supabase tables are expected to support simple upserts:

- `helpmate_documents`
  - primary key: `document_id`
  - columns: `document_id text`, `fingerprint text`, `file_name text`, `payload jsonb`, `user_id uuid`, `last_activity_at timestamptz`, `expires_at timestamptz`, `updated_at timestamptz`
- `helpmate_indexes`
  - primary key: `document_id`
  - columns: `document_id text`, `fingerprint text`, `collection_name text`, `payload jsonb`, `updated_at timestamptz`
- `helpmate_index_artifacts`
  - primary key: `fingerprint`
  - columns: `fingerprint text`, `document_id text`, `collection_name text`, `index_record jsonb`, `chunks jsonb`, `sections jsonb`, `synopses jsonb`, `topology_edges jsonb`, `updated_at timestamptz`

If you want the stricter authenticated retention model, also apply:

- [docs/sql/supabase-workspace-retention.sql](./sql/supabase-workspace-retention.sql)

That script adds:

- explicit `user_id`, `last_activity_at`, and `expires_at` columns
- cascading delete from document rows to index and artifact rows
- RLS policies so only the owning authenticated user can read active rows
- a `pg_cron` cleanup job that deletes expired workspaces every 5 minutes

If you run Supabase state with local VPS uploads/indexes:

- keep the Supabase SQL cleanup enabled for row expiry
- also run `python -m backend.maintenance` on the VPS every few minutes

Why:

- the Supabase cron deletes expired rows and cascades remote records
- the VPS sweeper deletes orphaned local uploads, local index directories, and stale answer-cache files
- this prevents expired files from lingering on disk when no user returns

This mode is the clean path if you want:

- one or a few documents per user
- managed remote persistence
- a cheaper stateless backend host

## Frontend Environment

Important frontend environment variables:

- `NEXT_PUBLIC_API_BASE_URL`
- `NEXT_PUBLIC_UPLOAD_API_BASE_URL`
- `API_REWRITE_TARGET`
- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`

Typical production setup on Vercel:

- `NEXT_PUBLIC_API_BASE_URL=/api`
- `NEXT_PUBLIC_UPLOAD_API_BASE_URL=https://api.yourdomain.com`
- `API_REWRITE_TARGET=https://api.yourdomain.com`
- `NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co`
- `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=...`

This keeps normal browser calls same-origin from the frontend point of view while the Next app proxies them to the backend, but lets larger uploads go directly to the API host.

## VPS Notes

The VPS bundle is the standard production backend path.

Recommended shape:

- Vercel keeps serving the frontend
- one Linux VPS runs the FastAPI backend
- Caddy terminates TLS and proxies to the backend container
- Supabase and hosted Chroma stay exactly as they are

If Cloudflare is active in front of the domain:

- keep `api` proxied
- keep `app` as the Vercel `CNAME`
- set Cloudflare SSL/TLS mode to `Full (strict)`
- keep the backend `HELPMATE_CORS_ORIGINS` aligned to the real app domain, for example:
  - `https://app.yourdomain.com`

Files included for that path:

- [deploy/vps/docker-compose.yml](C:\Users\Leander Antony A\Documents\Projects\HelpmateAI_RAG_QA_System\deploy\vps\docker-compose.yml)
- [deploy/vps/Caddyfile](C:\Users\Leander Antony A\Documents\Projects\HelpmateAI_RAG_QA_System\deploy\vps\Caddyfile)
- [deploy/vps/.env.example](C:\Users\Leander Antony A\Documents\Projects\HelpmateAI_RAG_QA_System\deploy\vps\.env.example)

Suggested first host size:

- `4 GB RAM` minimum if you trim the pipeline
- `8 GB RAM` preferred for the current pipeline and safer headroom

Suggested VPS rollout:

1. Create an Ubuntu VPS.
2. Point `api.yourdomain.com` at the VPS public IP.
3. Install Docker and Docker Compose.
4. Copy the repo onto the VPS.
5. Copy `deploy/vps/.env.example` to `deploy/vps/.env` and fill in your real secrets.
6. Set `HELPMATE_API_DOMAIN` to your API hostname.
7. Run `docker compose up -d --build` from [deploy/vps](C:\Users\Leander%20Antony%20A\Documents\Projects\HelpmateAI_RAG_QA_System\deploy\vps).
8. Wait for Caddy to provision TLS automatically.
9. Verify `https://api.yourdomain.com/health`.
10. Update the frontend proxy target if needed.

Recommended host cron entry for local-disk cleanup:

```cron
*/10 * * * * docker exec helpmate-api python -m backend.maintenance >> /var/log/helpmate-workspace-sweeper.log 2>&1
```

**Full crontab on the live VPS** (informational — what's actually scheduled):

```cron
# HelpmateAI workspace TTL sweeper (no LLM, pure DB+disk cleanup)
*/10 * * * * docker exec helpmate-api python -m backend.maintenance >> /var/log/helpmate-workspace-sweeper.log 2>&1

# AI Job Agent retention sweeper (no LLM, Supabase row cleanup only)
17 3 * * * docker exec ai-job-application-agent-api python -m backend.maintenance >> /var/log/aijobagent-retention-sweeper.log 2>&1

# Weekly docker prune (no LLM, just frees ~6 GB of dangling images)
30 3 * * Sun cd /home/ubuntu/HelpmateAI_RAG_QA_System/deploy/vps && sh ./cleanup-docker.sh >> /var/log/helpmate-docker-cleanup.log 2>&1
```

Zero scheduled LLM-spending jobs. The two pg_cron jobs on the Job Agent Supabase project (`cached_jobs_refresh_4h`, `cleanup-expired-resume-builder-sessions`) are also LLM-free.

### Nightly Evaluation Cron — MANUAL-ONLY MODE (current default)

The `backend.nightly_eval` CLI runs the existing RAGAS, FinanceBench, and combined final-eval suites and writes a structured JSON summary. It's the safety net against silent quality drift after a model upgrade, prompt edit, or index change.

**As of 2026-05-16 the scheduled cron is intentionally NOT installed.** Each full run costs ~\$1.5-3 in OpenAI (~600-1000 LLM calls); daily would burn \$45-90/month at pre-revenue stage. The decision and the re-enable trigger are captured in [ADR-020](adr/ADR-020-manual-only-nightly-eval-at-pre-revenue-stage.md).

**Manual one-off (no Sentry alert, no recurring cost):**

```sh
docker exec helpmate-api python -m backend.nightly_eval --output /tmp/eval.json
docker exec helpmate-api cat /tmp/eval.json | jq .metrics
```

Use it after shipping a meaningful model or prompt change. The script writes its structured summary to `data/nightly_eval/latest.json` inside the container.

**Three-step re-enable when revenue justifies the spend:**

1. **Capture baselines from one manual run.** Run the manual pattern above, save the `.metrics` dict to `data/nightly_eval/baselines.json` (the script also writes baselines automatically on first non-dry-run invocation).

2. **Uncomment the cron line.** The VPS crontab carries a documentation block with the recommended Mon+Thu schedule. The active line to uncomment:

   ```cron
   30 3 * * Mon,Thu docker exec helpmate-api python -m backend.nightly_eval --baselines data/nightly_eval/baselines.json --check-thresholds >> /var/log/helpmate-nightly-eval.log 2>&1
   ```

   Mon+Thu (~\$15-26/mo) gives 3-4 day drift-detection window. Daily (`30 3 * * *`, ~\$45-90/mo) is the original schedule documented before the cost analysis.

3. **Flip the Sentry Crons monitor env var.** Add to the VPS `.env`:

   ```
   HELPMATE_NIGHTLY_EVAL_MONITOR_ENABLED=true
   ```

   Then `docker compose up -d --force-recreate api` to load the new env. The Sentry `helpmate-nightly-eval` monitor will auto-recreate on the first check-in with the schedule already wired in code (`30 3 * * 1,4`). Regression alerts route through Sentry's "Cron failure" issue feed — no mail server needed.

`baselines.json` keys must match `TRACKED_METRICS` in `backend/nightly_eval.py` — currently `ragas_faithfulness`, `ragas_answer_relevancy`, `financebench_supported_rate`, and `final_eval_supported_rate`. A drop of more than 5% (configurable via `--regression-threshold-pct`) returns exit code 2, which is what triggers the Sentry Cron-failure issue when `--check-thresholds` is passed.

The script captures step-level failures into the `errors` array instead of aborting, so a transient OpenAI outage on the RAGAS step doesn't tank the whole run.

### Observability env vars

These ship the Sentry + PostHog stack live (see [ADR-018](adr/ADR-018-observability-stack-sentry-and-posthog.md)). All are optional — missing values reduce to a clean no-op. Set them in the VPS `.env`:

```
# Sentry
SENTRY_DSN=https://<key>@<org>.ingest.de.sentry.io/<project_id>
SENTRY_TRACES_SAMPLE_RATE=0.1
SENTRY_PROFILES_SAMPLE_RATE=0.05
SENTRY_SEND_DEFAULT_PII=false
# SENTRY_RELEASE falls back to retrieval_version when unset; explicit
# value useful if you bump it per deploy
SENTRY_RELEASE=

# PostHog (shared with AI Job Agent under the same free-tier project;
# every event auto-tags with product="helpmate" via capture_event)
POSTHOG_API_KEY=phc_<key>
POSTHOG_HOST=https://eu.i.posthog.com

# Environment label attached to every Sentry issue + PostHog event
HELPMATE_ENVIRONMENT=production

# Sentry Crons monitor for nightly_eval — leave FALSE while the cron
# is disabled, flip to TRUE after re-enabling the schedule
HELPMATE_NIGHTLY_EVAL_MONITOR_ENABLED=false
```

Frontend (Next.js) reads `NEXT_PUBLIC_*` equivalents from Vercel's env settings. The Vercel-Sentry integration auto-provisions `SENTRY_AUTH_TOKEN` so `withSentryConfig` uploads source maps; manual setup (paste the token directly into Vercel env) is the fallback if the integration UI conflicts with already-set env vars.

### Operational gotchas (lessons from Day 36)

#### Docker compose project-name discipline

The GHA deploy and the documented manual deploy both default to **project name = directory name = `vps`**. The named volumes are prefixed with the project name, so `vps_helpmate_uploads`, `vps_helpmate_indexes`, `vps_helpmate_cache` are the data-bearing volumes. Recreating the container with a different project name (`docker compose -p helpmate up -d`) silently mounts fresh empty volumes instead of remounting the originals — the container comes up healthy but with no data.

**Audit pattern** to catch this if you suspect a project-name drift:

```sh
# What volumes does the running container actually mount?
docker inspect helpmate-api --format \
  '{{range .Mounts}}{{println .Name "->" .Destination}}{{end}}'

# How many files are in each volume? (run from the VPS host)
for v in vps_helpmate_uploads vps_helpmate_indexes vps_helpmate_cache; do
  echo "$v: $(docker run --rm -v $v:/data alpine sh -c 'find /data -type f | wc -l') files"
done
```

If the running container is mounted on `helpmate_*` but data lives on `vps_*`, fix with:

```sh
cd /home/ubuntu/HelpmateAI_RAG_QA_System/deploy/vps
docker compose -p helpmate down                 # tear down wrong-project state
docker volume rm helpmate_helpmate_uploads helpmate_helpmate_indexes helpmate_helpmate_cache  # drop the empties
docker compose -p vps up -d --no-build api      # re-attach to the original volumes
```

#### Caddy state must be in git

The HelpmateAI Caddy container is the **single ingress for both HelpmateAI and AI Job Agent backends** — the Job Agent's compose override deliberately drops its own Caddy via the `shared_ingress / vps_default` network pattern. Both site blocks (`api.helpmateai.xyz` + `api.job-application-copilot.xyz`) MUST live in `deploy/vps/Caddyfile` and be committed. Any runtime edit via Caddy's admin API or in-container shell will silently delete on the next `docker restart helpmate-caddy`, taking the unbacked-up site config with it.

If `api.job-application-copilot.xyz` returns a 502 / 525 / "Just a moment" after a Caddy restart, the most likely cause is a missing site block. Verify:

```sh
docker exec helpmate-caddy cat /config/caddy/autosave.json | jq '.apps.http.servers'
```

If a domain is missing from `match.host`, append the site block to `deploy/vps/Caddyfile` and run `docker exec helpmate-caddy caddy reload --config /etc/caddy/Caddyfile`.

### Docker Image And Build-Cache Cleanup

Repeated backend rebuilds or image pulls can leave old untagged image layers and BuildKit cache on the VPS. Keep the current running backend image and named data volumes, but prune unused image/build artifacts after deploys.

The GitHub Actions deploy workflow copies `cleanup-docker.sh` to the VPS and runs it automatically after the `helpmate-api` health check passes. For manual deploys, use the wrapper script from `deploy/vps/`:

```sh
sh ./deploy-api.sh
```

If you build on the VPS instead of pulling from GHCR:

```sh
sh ./deploy-api-build.sh
```

The cleanup script runs:

- `docker image prune -a -f --filter "until=72h"`
- `docker builder prune -a -f --filter "until=72h"`

It deliberately does not run `docker system prune --volumes`, because the compose stack uses named volumes for uploads, indexes, cache, and Caddy state.

Do not keep a separate weekly Docker prune cron on the shared VPS. The GitHub Actions deploy workflow already runs this cleanup after a successful backend health check, and the 72-hour retention window leaves recently replaced images available for short rollback windows.

Remove the old weekly cron entry with:

```sh
crontab -l | grep -v 'helpmate-docker-cleanup.log' | crontab -
```

Use `docker system df` before and after cleanup if you want to inspect how much disk was recovered.

Important:

- Docker does not run cleanup hooks by itself just because an image is built or pulled.
- The GitHub Actions deploy workflow runs age-filtered cleanup after successful backend health checks.
- Use `deploy-api.sh` or `deploy-api-build.sh` as the standard manual deployment command so cleanup runs immediately after manual backend updates.
- If disk pressure returns, inspect `docker system df` before adding any host-level cleanup schedule back.

Recommended low-memory production default on smaller VPS plans:

- `HELPMATE_RERANKER_ENABLED=false`

Why:

- reranking is one of the heaviest live-query features in the current pipeline
- turning it off is the fastest way to reduce memory pressure without redesigning the architecture

## Local Dev Reminder

Local defaults stay simple:

- backend: `uv run uvicorn backend.main:app --reload --port 8001`
- frontend: `npm run dev` inside [frontend](C:\Users\Leander Antony A\Documents\Projects\HelpmateAI_RAG_QA_System\frontend)

The frontend rewrite defaults to `http://127.0.0.1:8001` locally, so local development still works without extra setup.
