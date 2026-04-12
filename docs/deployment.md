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
- [Dockerfile.streamlit](C:\Users\Leander Antony A\Documents\Projects\HelpmateAI_RAG_QA_System\Dockerfile.streamlit) for the retained internal Streamlit shell
- [render.yaml](C:\Users\Leander Antony A\Documents\Projects\HelpmateAI_RAG_QA_System\render.yaml) as a backend-oriented Render blueprint

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

## Local Dev Reminder

Local defaults stay simple:

- backend: `uv run uvicorn backend.main:app --reload --port 8001`
- frontend: `npm run dev` inside [frontend](C:\Users\Leander Antony A\Documents\Projects\HelpmateAI_RAG_QA_System\frontend)
- Streamlit shell: `streamlit run app.py`

The frontend rewrite defaults to `http://127.0.0.1:8001` locally, so local development still works without extra setup.
