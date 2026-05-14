# Tier-Enforcement Rollout — Flagged Items

Running record of items flagged during the 6-step tier-enforcement series
(see brief). Reviewed together at the end so we can decide what's an
explicit follow-up vs. acceptable as-shipped vs. needs a fix-up PR before
merging the series.

## Step 1 — Tier resolution shim (PR: feat/tier-resolution-shim)

1. **`resolve_user_tier(user)` signature** — reads `user.id` unconditionally.
   When payments land and we look up Stripe by Supabase UUID, that's the
   right key. If we'd rather pre-pass an `org_id` or anything else for
   Business-tier seat lookups, change the signature here once, not at every
   call site.

2. **`RETENTION_UNBOUNDED = -1` sentinel** — exported from `backend/tiers.py`.
   Step 6's sweeper should check `if retention_days < 0: skip` rather than
   `if retention_days == -1`, so any future "infinity sentinel" change
   (e.g. `math.inf` mapped to int) is contained.

3. **Monotonic-cap invariant test** (`test_higher_tier_never_has_smaller_cap`)
   will start failing if Pro ever beats Business on some axis. Intentional —
   if we ever introduce a Business-tier feature that limits something below
   Pro (unusual but possible for, e.g., "Business has stricter audit
   retention"), the test needs a per-field override list.

## Step 2 — Upload quota gates (PR: feat/upload-quota-gates)

1. **`check_file_size_cap` compares against `UploadFile.size`, not the
   request's Content-Length header.** The brief specifies Content-Length;
   in practice the header includes ~few hundred bytes of multipart envelope
   overhead per part, which would make a user's 25 MB file fail a 25 MB
   cap. `file.size` (post-multipart-parse) is the actual body size and
   matches the user's mental model. The brief's "file at exactly the cap →
   200 OK" only passes against `file.size`.

   Trade-off accepted: by the time the route runs, FastAPI has already
   parsed the multipart body — bytes are on disk/in memory. The "reject
   early" benefit is real (we skip pipeline + storage upload), just not
   "before any bytes hit the server" early. Truly-early would need ASGI
   middleware, which the brief Section 4 forbids.

2. **`check_content_length_present` is now separate from
   `check_file_size_cap`.** The brief framed both as a single
   Content-Length-based check, but missing-header and exceeds-cap are
   structurally different rejections (411 vs 413, defensive vs business
   rule). Splitting them keeps each function single-purpose.

3. **Doc-count cap is currently unreachable in production.** The upload
   handler still calls `_find_active_workspace_document` followed by
   `_delete_workspace_records` on the existing doc — the single-workspace-
   per-user model means `_count_active_documents` returns 0 or 1, never 3.
   The gate's structural presence means when multi-doc workspaces ship,
   the cap activates without any further wiring. Integration test patches
   `_count_active_documents` to simulate the cap being hit.

4. **No HTTP-level test infrastructure existed before this step.** The
   integration test introduces `TestClient` + dependency-override pattern
   for stub auth. Future steps that need HTTP coverage (Step 3 quota
   counters, Step 5 premium toggle) can reuse the same `authed_client`
   fixture.

5. **Type annotation `-> DocumentBundleResponse | JSONResponse`** on the
   upload handler is honest but slightly mismatched with FastAPI's
   `response_model=DocumentBundleResponse`. FastAPI handles `JSONResponse`
   returns transparently (bypasses response_model serialization on that
   branch). Worth keeping an eye on if OpenAPI schema generation matters
   for the frontend's API types.

## Step 3 — Monthly question quota

(to be filled in as we ship)

## Step 4 — Tier-aware answer model

(to be filled in as we ship)

## Step 5 — Premium answers + frontend toggle

(to be filled in as we ship)

## Step 6 — Retention TTL sweeper

(to be filled in as we ship)
