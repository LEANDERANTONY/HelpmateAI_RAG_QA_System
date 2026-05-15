# HelpmateAI docs index

This is the docs-governance file. It catalogs every tracked Markdown file in the repo, explains who each one is for and when to update it, and documents the criteria for keeping a file local vs. publishing it on the remote. Treat it as a living index — whenever a doc is added, promoted out of local, pruned, or substantially refactored, update the relevant row here in the same commit.

---

## Tracked docs

### Project-level

| File | Audience | Update trigger |
|---|---|---|
| [`README.md`](../README.md) | First-time visitor to the GitHub repo | Headline benchmark changes, tech-stack additions, links go stale, or a new top-level capability ships (e.g. payments going live) |
| [`docs/DEVLOG.md`](DEVLOG.md) | Future-me + external reviewers reading the chronology | Add a `## Day N` entry per substantial commit series (cadence ~weekly during heavy iteration). Latest entry on top. |

### Architecture + how-it-works

| File | Audience | Update trigger |
|---|---|---|
| [`docs/architecture.md`](architecture.md) | New contributor or external reviewer reading the full RAG core | Pipeline-shape change (new retrieval stage, new layer, new observability surface, retired component) |
| [`docs/architecture-flow.md`](architecture-flow.md) | Same audience, faster overview | Refresh when the end-to-end flow diagram changes; smaller drift is OK |
| [`docs/frontend-reference.md`](frontend-reference.md) | Frontend contributor + design partner | Direction-B + framer reference patterns change, or a new shared design language ships |
| [`scripts/SUPABASE_STORAGE_SETUP.md`](../scripts/SUPABASE_STORAGE_SETUP.md) | Operator wiring a fresh Supabase project's Storage bucket | Bucket policy / IAM model changes |

### Architecture Decision Records

| File | Audience | Update trigger |
|---|---|---|
| [`docs/adr/ADR-NNN-*.md`](adr/) | Anyone wondering "why this decision was made" | **NEVER edit existing ADRs** — they're historic by design. If the decision changes, add a new ADR that supersedes it. Mark superseded ADRs with a status note rather than rewriting them. |
| [`docs/adr/README.md`](adr/README.md) | ADR index page | Update **every time** a new ADR lands. Add the new file to the right thematic cluster (Core RAG / Tier+Payments / Observability+Compliance) and add a one-line summary in the "Current state note" block if the decision changes the production picture. |

### Operations

| File | Audience | Update trigger |
|---|---|---|
| [`docs/deployment.md`](deployment.md) | Operator deploying, troubleshooting, or onboarding to the VPS | Env var changes, cron changes, infra topology changes, new operational gotcha that bit hard enough to need a runbook entry |
| [`docs/lemon-squeezy.md`](lemon-squeezy.md) | Operator setting up Lemon Squeezy for the first time | LS event-mapping changes, new variant ID / pricing tier, webhook URL or secret rotation |

### Evaluation methodology

| File | Audience | Update trigger |
|---|---|---|
| [`docs/evals/README.md`](evals/README.md) | First-time reader looking at the eval surface | New eval suite added, methodology summary changes |
| [`docs/evals/EVAL_ROADMAP.md`](evals/EVAL_ROADMAP.md) | Anyone planning the next eval iteration | Major eval-direction decisions; not for tactical work-tracking |
| [`docs/evals/ARCHITECTURE_SCORECARD.md`](evals/ARCHITECTURE_SCORECARD.md) | Reader auditing the architectural-ablation evidence | New ablation study lands with a saved report |
| [`docs/evals/benchmark_summary.md`](evals/benchmark_summary.md) | Reader comparing HelpmateAI vs vendor baselines | New vendor rerun or new public benchmark snapshot |
| [`docs/evals/final_eval_protocol.md`](evals/final_eval_protocol.md) | Anyone reproducing the held-out final eval | Protocol changes (new metrics, new scoring rules, new abstention semantics) |
| [`docs/evals/final_eval_question_authoring_prompt.md`](evals/final_eval_question_authoring_prompt.md) | Author writing or reviewing held-out questions | Question-authoring rubric changes |
| [`docs/evals/final_eval_sources_20260428.md`](evals/final_eval_sources_20260428.md) | Reader auditing the source documents used in the held-out suite | New suite generated against different sources (rename per-date) |
| [`docs/evals/financebench_protocol.md`](evals/financebench_protocol.md) | Anyone reproducing the FinanceBench portion | Protocol changes specific to FinanceBench scoring |
| Reports under `docs/evals/reports/` | Historical record | **Append-only.** Never edit a saved report; generate a new one with a new timestamp instead. |

### Frontend agent instructions

| File | Audience | Update trigger |
|---|---|---|
| [`frontend/AGENTS.md`](../frontend/AGENTS.md) | Claude Code / LLM agents working on the frontend | Coding conventions change, new lint rule or build path matters for autonomous edits |
| [`frontend/CLAUDE.md`](../frontend/CLAUDE.md) | Claude Code convention pointer (`@AGENTS.md`) | Only update if the pointer target moves — almost never |
| [`frontend/README.md`](../frontend/README.md) | First-time visitor to the `frontend/` package | Build / dev / lint commands change, package layout reorganizes |

### Schema migrations (informational)

The `docs/sql/*.sql` files are reference copies of the Supabase migrations applied to production. They're tracked so a fresh-DB redeploy can rebuild the schema without paging through the Supabase Studio history.

| File | What it sets up |
|---|---|
| `docs/sql/supabase-feedback.sql` | `helpmate_feedback` table + RLS |
| `docs/sql/supabase-quota-counters.sql` | `helpmate_quota_counters` + atomic `increment_question_counter` RPC + tier matrix |
| `docs/sql/supabase-run-traces-cost-columns.sql` | Adds `prompt_tokens` / `completion_tokens` / `cost_usd` / `model_name` columns to `helpmate_run_traces` |
| `docs/sql/supabase-subscriptions.sql` | `subscriptions` + `subscription_webhook_log` tables for the LS integration |
| `docs/sql/supabase-workspace-retention.sql` | Workspace TTL columns + RLS (the SQL pg_cron sweeper was deprecated; the Python sweeper in `backend/maintenance.py` is the active path) |

Update trigger: only when a new migration lands. Old `.sql` files are append-only.

---

## Untracked files (local-only, gitignored)

These exist on the developer machine and the VPS but never on the remote. They carry working-context value but don't belong in the public repo.

| File | Why local |
|---|---|
| `AGENT.md` (repo root) | Working briefing for new chat agents — codebase layout, infra topology, Supabase inventory, observability wiring, VPS cron list, the painful-things runbook, sibling-project coupling notes. Carries VPS hostnames and operational detail that's working-briefing material rather than a public README artifact. Rebuild from commit history if it's lost (the git log message on the AGENT.md gitignore commit describes what it should contain). |
| `docs/history/HelpmateAI_RAG_project_Cleaned.ipynb` | Original prototype Jupyter notebook from before this became a full app. Kept on disk as a portfolio artifact. The production RAG core lives in `src/pipeline/` + `backend/`. |
| `design_system/` | Internal design specs (landing + workspace UI specifications, inspiration images, PACKAGE notes). Substantive content but not part of the public docs surface. |

---

## Pruning criteria — when to delete a tracked doc

A tracked doc is a candidate for removal when **any one** of these is true:

1. **Superseded by another tracked doc.** Two docs on the same topic creates two sources of truth that drift. Pick the canonical one, fold useful content from the other into it, and delete the redundant file.
2. **It documented a recipe that's now fully shipped + tested.** "Migration recipes" and "wiring instructions" docs have a fixed lifespan — once the migration is done and the tests are green, the recipe is reference material that can usually be deleted (the relevant ADR + the code itself become the durable record).
3. **Its own preamble says it's not for public consumption** (e.g. literally states "not part of the public README story"). If it's been tracked anyway, the gap between intent and reality is the signal to either commit to publishing it or move it to local-only.
4. **The decision/context it captured is no longer accurate.** Stale architecture docs are worse than no docs because a reader trusts them. If a doc would need a complete rewrite to be accurate, prefer rewriting as a new doc + deleting the stale one, rather than incremental edits that leave half-truths in place.

Before deleting, check whether the doc is referenced from another tracked file (use `grep -r "filename" docs/ README.md` to be safe) and either update the references or remove them in the same commit.

---

## Local-only criteria — when a doc shouldn't be tracked

A new doc should stay untracked (and explicitly added to `.gitignore`) when **any one** of these is true:

1. **It contains operational secrets, hostnames, or infra IDs** that don't belong in a public repo. The `.env` file is the canonical example. `AGENT.md` is the borderline case — it has VPS hostnames + SSH port + Supabase project IDs that are working details but not actually credentials.
2. **Its purpose is a working briefing rather than a durable artifact.** A "what-I-want-the-next-agent-to-know" file lives differently from an ADR — the working briefing is allowed to be edited freely without versioning anxiety.
3. **It's a personal scratchpad** (TODO lists, draft plans, exploratory notes). Don't drag the team into your private thinking layer. If a piece of it becomes durable, promote it into a tracked doc later.
4. **It's auto-generated** (eval reports under `docs/evals/reports/` are an edge case — those ARE tracked because they're checkpoint artifacts, but anything regenerated on every run shouldn't be).

When adding a local-only doc, add it to `.gitignore` in the same commit that creates it, with a brief comment explaining why it's local. Future-you reading `.gitignore` should be able to understand the policy without spelunking.

---

## Adding a new tracked doc

1. **Pick the right home.** Decision records go under `docs/adr/`. Operational guidance under `docs/`. Eval material under `docs/evals/`. Code-package-specific docs (like `frontend/README.md`) stay next to their code.
2. **Add an entry to this README's tables** above. The audience + update trigger fields are the load-bearing parts — without them, future-you (or a contributor) doesn't know whether the doc is supposed to be edited weekly or only on a specific event.
3. **Cross-link from the canonical readers.** If the new doc explains something covered (or referenced) in `README.md`, `architecture.md`, or `deployment.md`, add the link. Orphan docs rot.
4. **Commit the new doc + this README update + any cross-links** in the same commit. Don't let docs governance lag behind the actual docs.

---

## Maintenance cadence

This file gets updated on the same commit that:

- Adds a new doc
- Promotes a local-only file to tracked (rare)
- Demotes a tracked file to local-only or deletes it
- Materially changes the audience or update trigger of an existing doc

Plain Markdown edit + commit; no automation. The point of the file is to be cheap to maintain so it stays accurate.
