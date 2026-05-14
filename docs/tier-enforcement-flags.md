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

## Step 3 — Monthly question quota (PR: feat/question-quota)

1. **Pre-check + post-increment, not atomic-pre-increment.** The brief
   showed both patterns; I went with the "increment AFTER successful
   generation" recommendation rather than the example's "increment then
   check". Trade-off: a small race window where two concurrent /qa
   requests at cap-1 can both pre-check pass and both run pipeline,
   producing N+1 actual answers on a cap of N. Counter ends at N+1 and
   subsequent calls reject. Accepted because the alternative (atomic
   pre-increment + rollback on failure) is meaningfully more complex
   for no real-world benefit at our scale.

2. **Pipeline failure does NOT decrement.** Per brief, the increment only
   runs on success — pipeline raises → no increment, user can retry. We
   don't have a "rollback on failure" path because we never incremented
   in the first place. Cleaner than the example's atomic-then-undo flow.

3. **`LocalQuotaStore` uses a single JSON file with read-modify-write,
   no locking.** Fine for dev/tests (single-process). Production runs
   `SupabaseQuotaStore` which uses the atomic RPC. Don't deploy the
   local backend in prod (it's already the wrong choice for state
   anyway — `state_store_backend=supabase` is the production setting).

4. **`current_period_start()` accepts an optional `now` param for tests
   but is called as `current_period_start()` everywhere in production.**
   The Local store's month-rollover test monkey-patches the function
   instead of injecting `now`. If we ever add a "set quota period" admin
   tool, that's the seam.

5. **RPC functions are SECURITY DEFINER + service_role only.** Caught
   while drafting these notes: my initial migration granted EXECUTE to
   `authenticated`, which would have let any signed-in user call
   `supabase.rpc('increment_question_counter', {p_user_id: '<victim>'})`
   to burn another user's quota. Fixed in the same migration — now
   granted to `service_role` only. The backend uses the service-role
   key for Supabase access, so backend-driven increments still work;
   client-side calls fail with permission-denied. If we ever want to
   expose this to authenticated clients (we shouldn't), add a
   `p_user_id = auth.uid()` check inside the function.
   **[Partial gap caught post-merge, fixed]** The original migration
   revoked from `public` and `authenticated` but missed `anon` — which
   Supabase grants EXECUTE on public-schema functions by default. An
   unauthenticated caller could have hit the same victim-quota-burn
   attack via the public anon key. Closed by Supabase migration
   `revoke_anon_quota_rpcs` (applied 20260514154130) AND backported
   into `docs/supabase-quota-counters.sql` so a fresh-DB redeploy is
   secure out of the box.

6. **Return-type annotation `-> AskResponse | JSONResponse` on /qa**,
   same caveat as Step 2's upload handler. FastAPI handles it at
   runtime; OpenAPI schema may not reflect both branches.

7. **`HELPMATE_SUPABASE_RUN_TRACES_TABLE` was missing from `.env.example`
   even though it's in `Settings`.** I added the new
   `HELPMATE_SUPABASE_QUOTA_COUNTERS_TABLE` for completeness; didn't
   backfill the missing one for run_traces (out of this PR's scope).
   **[Resolved]** Backfilled in `chore/tier-enforcement-followups`.

## Step 4 — Tier-aware answer model (PR: feat/tier-aware-model)

1. **`settings.answer_model` is preserved as the fallback.** Per brief —
   eval scripts and any unauthenticated context still resolve to the env
   var. The `/qa` handler is the one place that overrides it (passing
   `TIER_LIMITS[tier]["answer_model"]`). When payment integration ships,
   only `resolve_user_tier` changes; this plumbing keeps working.

2. **Cache key includes the active model.** Without this, a free-tier
   nano answer would be served to a pro-tier user asking the same
   question (sandbagging the paid tier). The cache key formula is
   `(fingerprint, question, retrieval_version, generation_version,
   model_name)` — `model_name` now reflects the override when present.
   Side effect: free + pro have separate cache namespaces, so cache
   hit rates per tier are independent. Worth tracking when telemetry
   lands.

3. **`AnswerGenerator.verify_supported_answer` was NOT updated to use
   model_override.** It still reads `self.settings.answer_model` directly
   (line 319 in src/generation/service.py). The verifier is a separate
   guardrail concern — its purpose is to double-check the primary
   answer's claim of support; using the SAME tier-specific model would
   defeat the verifier's independent-second-opinion purpose. If we ever
   want tier-aware verification (e.g., business gets a smarter
   verifier), that's a follow-up.

4. **Recovery-path `generate_answer` calls also receive model_override.**
   The pipeline's abstention-recovery branch (when initial answer is
   unsupported, retrieval is re-tried, generation runs again) passes
   model_override through too — otherwise the second-attempt answer
   would silently degrade to settings.answer_model.

5. **`verify_support_status` (separate from `verify_supported_answer`)
   keys off `support_status_verifier_model`** — not `answer_model`. So
   it's unaffected by the model_override plumbing. The two verifier
   methods have confusingly similar names; might be worth renaming in
   a future cleanup PR.

6. **One existing test broke and was fixed**:
   `test_run_traces.py::test_recovery_verifier_can_keep_recovered_answer_as_partial`
   was monkey-patching `generate_answer` with `lambda *_:` which didn't
   accept the new `model_override=` keyword arg. Updated to
   `lambda *_args, **_kwargs:` for forward-compat with future signature
   changes too.

## Step 5 — Premium answers + frontend toggle (PR: feat/premium-answers)

1. **Premium increments BOTH counters per spec.** Brief recommended
   "clearer to BOTH count toward standard AND charge a premium credit,
   since the underlying call still happens" — followed verbatim. A
   pro user with 25/25 premium and 500/500 standard would hit the
   premium-cap rejection first. A pro user with 25/25 premium and
   495/500 standard who asks 5 premium questions ends at 500/500
   standard AND 30/25 premium — wait, no: each premium call
   increments standard once. So 5 premium calls = 5 std + 5 premium,
   ending at 500/500 + 30/25. The premium cap fires first AND the
   standard cap fires at the same boundary. Both fields rendered to
   the user in the toast give the full picture.

2. **Two distinct 402 codes for premium.** `premium_unavailable` (tier
   has no premium_model — free) and `premium_quota_exhausted` (tier
   supports it but user is at cap). Frontend toast can branch copy:
   the former wants an upgrade CTA, the latter wants "resets on the
   1st" copy. Both share the same JSONResponse shape from quota.py.

3. **Gate order in /qa**: premium check (when premium=true) → standard
   question quota → pipeline → increment(s). Premium-only failure
   surfaces clearly; if both quotas are exhausted, the user sees the
   premium message first because it gates first.

4. **Premium toggle resets after each submission.** Captured into a
   local `useThisTurnPremium` variable, store cleared immediately so
   the next question defaults back to standard. Spec: "opt-in
   per-question, not sticky."

5. **Frontend trusts nothing.** `premium: true` from a free-tier client
   is rejected backend-side with `premium_unavailable`. The toggle is
   disabled in the UI for free users (defense-in-depth + correct UX),
   but the gate is the authority. The disabled state pulls from
   `quotaSnapshot.premium_available` which the backend computes from
   `TIER_LIMITS[tier]["premium_model"] is not None`.

6. **/workspace/quota is a fresh read every call** — no in-process
   cache. Frontend calls it on workspace mount + after every /qa
   (success or failure). One counter read + one doc-count scan per
   call; trivial cost. If usage explodes, the doc-count scan is the
   first to optimize via a `count(*) where user_id = ?` query at the
   store layer.

7. **`quotaSnapshot=null` before first fetch.** Toggle renders disabled
   until quota resolves. Brief implication: the user can't enter
   Read Mode and immediately request premium — there's a one-render
   delay. Acceptable; quota fetch is sub-100ms.

8. **Documents.used in /workspace/quota uses `_count_active_documents`**
   — same read-only scan as the upload doc-count gate. If multi-doc
   workspaces ship and per-user counts climb, this and the upload
   gate both want the same store-level optimization.

9. **`UPGRADE_URL` is hard-coded to `https://helpmateai.xyz/landing#pricing`.**
   Lives in `backend/quota.py`. If we ever rename the domain or split
   pricing onto its own page, change here only.
   **[Resolved]** `chore/tier-enforcement-followups` reads the URL
   from `HELPMATE_UPGRADE_URL` (default `https://helpmateai.xyz/pricing`,
   matching the production landing where pricing is at `/pricing`).

10. **Frontend tests deferred.** No unit tests on the toggle component
    yet — the workspace currently has no React-component test
    infrastructure (Jest/Vitest not configured). Manual smoke test
    only. Adding component tests is a follow-up that should land
    before next significant Read Mode work.

11. **`refreshStarters` and `refreshQuota` are component-scope
    functions called from a useEffect — neither in deps.** Existing
    pattern for refreshStarters silently passed lint; refreshQuota
    triggered the rule. Added an eslint-disable-next-line at the deps
    array with rationale comment. The cleaner fix (useCallback wrap)
    is deferred; it's a refactor of the existing pattern, not a Step
    5 concern.

## Step 6 — Retention TTL sweeper (PR: feat/tier-retention)

1. **One `expires_at` field, tier-based duration.** Re-interpreted the
   brief's "they coexist" pragmatically: the per-tier retention
   REPLACES the 24h delta inside `_touch_document_workspace` rather
   than adding a parallel `_workspace_hard_expires_at` field. The
   sweeper's existing "delete when `expires_at < now`" check works
   unchanged. `workspace_retention_hours` env var stays in Settings
   as a fallback for non-tiered callers (eval scripts via
   `_retention_delta()` without a user).

2. **Business tier removes `expires_at` from metadata entirely.**
   Sentinel-free design: rather than a far-future timestamp (year
   9999) that could outlive the tier sentinel constant, the field is
   stripped on touch. The sweeper's `expires_at is None` short-circuit
   handles "never delete" naturally. Tier downgrade (Business → Pro)
   resets the field cleanly on the next touch.

3. **Sweeper now routes deletions through FileStorage.** The existing
   `pipeline.delete_workspace` unlinks LOCAL paths only, which is a
   no-op for Supabase bucket keys. Added an inline
   `_delete_storage_files(storage, document)` helper in
   `backend/maintenance.py` that mirrors `main.py::_delete_storage_files`.
   Two near-identical helpers; if a third caller appears, factor into
   `backend/file_storage.py`.

4. **`pipeline.delete_workspace` runs BEFORE FileStorage cleanup.** The
   pipeline may need local files during teardown (cache invalidation,
   run-trace bookkeeping) — running storage cleanup first would yank
   them out from under it on the local backend. Order matters; the
   test verifies it indirectly via mock-call sequence.

5. **`_retention_delta_for_user` has a defensive fallback** to the env
   var when `retention_days <= 0` (excluding the -1 RETENTION_UNBOUNDED
   sentinel which is handled earlier). Unreachable under current
   TIER_LIMITS (free/pro have positive day counts, business is -1),
   but keeps the function total for future tiers that might land with
   a quirky retention spec.

6. **Sweeper tests mock the entire dependency tree.** `build_*`
   factories monkey-patched at the maintenance-module level rather
   than constructing real stores — the real LocalApiRecordStore +
   LocalFileStorage need on-disk state that's painful to set up just
   to verify which methods got called. Trade-off: tests don't catch
   integration drift between the sweeper and the real stores. Tier 1
   tests (run_traces sweep, cache file sweep) elsewhere in the suite
   exercise those code paths against real impls.

7. **Pre-existing sweep summary fields kept**: orphan_uploads_deleted,
   orphan_index_dirs_deleted, etc. The new tier-retention behavior
   changes WHEN `expired_workspaces_deleted` fires (longer windows)
   but not WHAT the summary contains. Telemetry stays compatible.

8. **No frontend change for Step 6.** Retention is a backend concern;
   the user-visible signal is "your workspace expired" via the
   existing 410 path in `_require_document_for_user`. The longer
   windows just mean fewer expirations.
