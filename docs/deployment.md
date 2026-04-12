# Deployment

HelpmateAI now deploys cleanly as a split product:

- Framer for the marketing front door
- `Next.js` for the product workspace
- `FastAPI` for uploads, indexing, QA, citations, and evidence

This is the recommended production shape:

- `www.helpmate.ai` -> Framer
- `app.helpmate.ai` -> `Next.js`
- `api.helpmate.ai` -> `FastAPI`

## Recommended Hosting

- Framer for the landing page
- Vercel for [frontend](C:\Users\Leander Antony A\Documents\Projects\HelpmateAI_RAG_QA_System\frontend)
- Render, Railway, Fly.io, or a VPS for the FastAPI backend

The repo includes:

- [Dockerfile](C:\Users\Leander Antony A\Documents\Projects\HelpmateAI_RAG_QA_System\Dockerfile) for backend deployment
- [render.yaml](C:\Users\Leander Antony A\Documents\Projects\HelpmateAI_RAG_QA_System\render.yaml) as a backend-oriented Render blueprint
- [deploy/vps/docker-compose.yml](C:\Users\Leander Antony A\Documents\Projects\HelpmateAI_RAG_QA_System\deploy\vps\docker-compose.yml) for a simple VPS deployment
- [deploy/vps/Caddyfile](C:\Users\Leander Antony A\Documents\Projects\HelpmateAI_RAG_QA_System\deploy\vps\Caddyfile) for TLS and reverse proxying on a VPS

## Deployment Flow

1. Deploy the FastAPI backend first.
2. Confirm the backend health endpoint returns `ok`.
3. Deploy the `Next.js` frontend.
4. Point the frontend at the backend using either:
   - same-origin `/api` rewrites
   - or a direct `NEXT_PUBLIC_API_BASE_URL=https://api.yourdomain.com`
5. Update Framer CTA buttons such as `Open workspace` and `Get started` to point to `https://app.yourdomain.com`.
6. Test upload, indexing, QA, citations, and evidence on the deployed URLs.

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

If you want to avoid a persistent Render disk, Helpmate can now run in a cloud-backed mode:

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

If the hosted Chroma endpoint requires headers, use:

- `HELPMATE_CHROMA_HTTP_HEADERS=Authorization=Bearer your_token`

The Supabase tables are expected to support simple upserts:

- `helpmate_documents`
  - primary key: `document_id`
  - columns: `document_id text`, `fingerprint text`, `file_name text`, `payload jsonb`, `updated_at timestamptz`
- `helpmate_indexes`
  - primary key: `document_id`
  - columns: `document_id text`, `fingerprint text`, `collection_name text`, `payload jsonb`, `updated_at timestamptz`
- `helpmate_index_artifacts`
  - primary key: `fingerprint`
  - columns: `fingerprint text`, `document_id text`, `collection_name text`, `index_record jsonb`, `chunks jsonb`, `sections jsonb`, `synopses jsonb`, `topology_edges jsonb`, `updated_at timestamptz`

This mode is the clean path if you want:

- one or a few documents per user
- managed remote persistence
- a cheaper stateless backend host

## Frontend Environment

Important frontend environment variables:

- `NEXT_PUBLIC_API_BASE_URL`
- `API_REWRITE_TARGET`

Typical production setup on Vercel:

- `NEXT_PUBLIC_API_BASE_URL=/api`
- `API_REWRITE_TARGET=https://api.yourdomain.com`

This keeps browser calls same-origin from the frontend point of view while the Next app proxies them to the backend.

## Render Notes

The included [render.yaml](C:\Users\Leander Antony A\Documents\Projects\HelpmateAI_RAG_QA_System\render.yaml) assumes:

- one backend web service
- Docker deploy using the root [Dockerfile](C:\Users\Leander Antony A\Documents\Projects\HelpmateAI_RAG_QA_System\Dockerfile)
- a mounted persistent disk at `/var/data/helpmate`

Before using it in production:

- replace `https://app.example.com` in `HELPMATE_CORS_ORIGINS`
- set `OPENAI_API_KEY`
- verify the plan and disk size are appropriate for your documents

## VPS Notes

If Render becomes too expensive for the memory you need, Helpmate now has a straightforward VPS path.

Recommended shape:

- Vercel keeps serving the frontend
- one Linux VPS runs the FastAPI backend
- Caddy terminates TLS and proxies to the backend container
- Supabase and hosted Chroma stay exactly as they are

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

Recommended low-memory production default on smaller VPS plans:

- `HELPMATE_RERANKER_ENABLED=false`

Why:

- reranking is one of the heaviest live-query features in the current pipeline
- turning it off is the fastest way to reduce memory pressure without redesigning the architecture

## Local Dev Reminder

Local defaults stay simple:

- backend: `uv run uvicorn backend.main:app --reload --port 8001`
- frontend: `npm run dev` inside [frontend](C:\Users\Leander Antony A\Documents\Projects\HelpmateAI_RAG_QA_System\frontend)
- Streamlit shell: `streamlit run app.py`

The frontend rewrite defaults to `http://127.0.0.1:8001` locally, so local development still works without extra setup.
