# Supabase Storage cutover playbook

This is the step-by-step for switching HelpmateAI's PDF/DOCX storage from
the VPS disk to Supabase Storage. Run through it once on the production
project; the dev box can stay on `local` indefinitely.

## Why we're doing this

`Unlimited documents` on the Pro tier is marketing fiction while bytes
live on the VPS — a 40 GB Hetzner box fills up after ~260 docs at the
150 MB Pro file cap. Moving to Supabase Storage:

- **Caps** are decoupled from the box we run code on. Storage scales
  independently and costs $0.021/GB-month after the 1 GB free tier.
- **Read bandwidth** doesn't go through our VPS. PDF.js downloads PDFs
  directly from Supabase's CDN via a 302 redirect to a signed URL.
- **Per-user partitioning** is free — bucket keys are namespaced
  `{owner_id}/{document_id}{ext}` so deleting a user wipes their entire
  prefix in one call.

## Pre-flight checklist

1. The deployed backend is on commit `991a00e` or newer (file_storage
   abstraction in place; default backend still `local`).
2. The Supabase project that owns the `helpmate_documents` /
   `helpmate_indexes` tables is the same one we'll create the bucket in
   (so the existing service-role key has Storage permissions).
3. You have the **service-role key**, not the anon key. The migration
   script needs it to upload files server-side. It's in the Supabase
   dashboard under Project Settings → API → Service Role Key (the one
   labeled "secret").

## Step 1 — Create the bucket

In the Supabase dashboard:

1. Go to **Storage** in the left nav → **New bucket**.
2. Name: `helpmate-documents` (must match `HELPMATE_SUPABASE_STORAGE_BUCKET`).
3. **Public bucket: OFF**. We serve via signed URLs from the backend;
   never make the bucket public — that would let anyone with a
   guessable key download other users' documents.
4. File size limit: leave at default (50 MB) for now if you want a
   sanity-check ceiling. The Business tier promises 500 MB so raise
   the bucket limit to at least 500 MB before Business launches.
5. Allowed MIME types: leave open (we validate `.pdf` / `.docx` at the
   application layer, and there's no harm letting the bucket accept
   anything else).

## Step 2 — RLS / access policies

**Don't add RLS policies for this bucket.** The backend always reads /
writes with the service-role key, which bypasses RLS. Adding RLS would
add complexity without security benefit because the application layer
already gates access via the `_require_document_for_user` check before
any storage call.

If you ever want client-direct uploads (skipping the backend), revisit
this and add a policy keyed on `bucket_id = 'helpmate-documents' AND
(storage.foldername(name))[1] = auth.uid()::text` — but that's a future
problem.

## Step 3 — Update environment

On the production VPS, edit `.env` (or your secrets manager):

```bash
# File storage backend selection. The actual flip happens in Step 5.
HELPMATE_FILE_STORAGE_BACKEND=local           # stay local during migration
HELPMATE_SUPABASE_STORAGE_BUCKET=helpmate-documents

# Make sure these are also set (probably already are, for state store).
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<service-role-key>
```

Don't restart the backend yet. We want it serving `local` while the
migration script runs so the read endpoint keeps working for already-
ingested docs (the script uploads to Supabase but doesn't break local
access until paths are rewritten — and even then, the read endpoint
will accept either form once we flip).

## Step 4 — Run the migration

Dry-run first to see what would happen:

```bash
cd /opt/helpmate  # or wherever the repo lives on the VPS
.venv/bin/python scripts/migrate_files_to_supabase.py
```

The output lists every document, what it'd upload, and a summary at the
end. If counts look right (matches `helpmate_documents` row count, no
unexpected "no owner_id" rows, etc.), apply for real:

```bash
.venv/bin/python scripts/migrate_files_to_supabase.py --apply
```

This uploads each source + viewable PDF to Supabase under
`{owner_id}/{document_id}.{ext}` and rewrites the DocumentRecord's
`source_path` / `viewable_pdf_path` fields to bucket keys. **Local
files are preserved** — they stay on the VPS disk as a fallback.

Verify in the Supabase dashboard: Storage → `helpmate-documents` should
now show one folder per user with the migrated files inside.

## Step 5 — Flip the backend env var

Now switch:

```bash
HELPMATE_FILE_STORAGE_BACKEND=supabase
```

Restart the backend. Upload a fresh document and check:

1. The file appears in Supabase Storage under your owner_id prefix.
2. Read Mode in the workspace opens the PDF (it should 302 redirect
   to a Supabase signed URL — visible in browser devtools network tab).
3. The download button works (also a 302, but with
   `Content-Disposition: attachment` set).
4. Deleting the workspace removes the bucket object.

If anything's broken, flip back to `local` — the migration left the
files on disk, so local-backend reads still work for migrated docs.
The records will have bucket keys in `source_path` which won't resolve
on the local backend, so the `uploads_dir` fallback in the read
endpoint (which recomputes the canonical local path from
`{document_id}{ext}`) will pick up the bytes from disk.

## Step 6 — (Optional) Reclaim VPS disk

Once you're confident the Supabase backend is working in prod for at
least a few days, you can either:

**Option A — Script the cleanup:**

```bash
.venv/bin/python scripts/migrate_files_to_supabase.py --apply --cleanup-local
```

This walks all DocumentRecords again, skips ones whose paths are
already bucket keys (so it's idempotent), and deletes local copies for
records that have been confirmed migrated.

**Option B — Manual:**

```bash
# After verifying Supabase serves everything, just nuke the uploads dir.
rm -rf /var/data/helpmate/uploads/*
```

The retention sweeper handles new local files automatically.

## Bucket cost monitoring

Watch the Supabase dashboard's Storage page. Free tier limits:

- 1 GB stored
- 5 GB bandwidth / month
- 50 MB per-file upload max (raise this before Business launches)

When storage approaches 800 MB or bandwidth approaches 4 GB, upgrade
to Supabase Pro ($25/mo) for 100 GB storage + 200 GB bandwidth. The
breakpoint is roughly 5-10 active paying users with typical doc loads.

## Rolling back

The migration script makes one of two changes per record:
- Uploads a file to Supabase (idempotent — upsert)
- Rewrites two string fields in a DB row

To roll back: edit `source_path` and `viewable_pdf_path` back to
absolute local paths in `helpmate_documents`. The local files are
still present (assuming you didn't run `--cleanup-local`), so the
local backend will serve them again. No data loss either way.

## After cutover

- VPS can downsize from a disk-heavy plan to a small CPU-only plan —
  10 GB SSD is plenty for the OS, logs, and the chroma index cache.
- The `HELPMATE_UPLOADS_DIR` is still required (FastAPI's UploadFile
  buffers there briefly during ingest), but it stays effectively empty
  — files are uploaded to Supabase as soon as ingest finishes and
  cleaned up locally.
