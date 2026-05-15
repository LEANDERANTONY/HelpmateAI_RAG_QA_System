# ADR-020: Manual-Only `nightly_eval` at Pre-Revenue Stage

Date: 2026-05-16

Status: Active (auto-revert when revenue justifies)

## Context

The Production Safety Pack (Day 31) introduced `backend/nightly_eval.py` — a CLI that runs the full eval suite (RAGAS + FinanceBench + final_eval_suite) programmatically, captures pass/fail counts + key metrics, writes a structured JSON summary, and exits non-zero on regression beyond a configurable threshold. The recommended cron line in `docs/deployment.md` runs it daily at 3:30 UTC. Day 35 added a Sentry Crons monitor (slug `helpmate-nightly-eval`) that expects a daily heartbeat and fires a missed-heartbeat alert if no check-in lands.

The first time the cron was installed (mid-Day 35), a re-check of the cost shape produced an uncomfortable number:

- Each full run = ~600-1000 LLM calls (RAGAS judges + FinanceBench questions + final_eval prompts)
- Estimated cost: **~\$1.5-3 in OpenAI per invocation** at gpt-5.4-mini pricing
- Daily cadence: **~\$45-90/month**, every month, forever

At pre-revenue stage with zero paying users, that's a meaningful fraction of total OpenAI spend going to a safety net that protects against "a future model upgrade silently regresses quality" — a real failure mode, but one that hasn't materialized yet and that would also be caught (slower, more painfully) by user complaint signals.

The decision had to balance:

- **Real protection value.** The nightly eval is genuinely the right tool to catch silent drift after a model upgrade. The day OpenAI swaps a model under our nose (which has happened — gpt-5-mini snapshot rotations) is exactly when we want today's metrics on yesterday's baseline.
- **Real cost.** ~\$45-90/mo with no users is a hard line at MVP stage. Anything that doesn't directly support shipping product or unblocking the first paid customer is on the chopping block.
- **Reversibility.** Whatever we do now must be one env-var-or-cron-line away from "back on" the moment a paid user lands and the COGS math flips.

## Decision

Switch `nightly_eval` to **manual-only mode** by default. The script still exists, is still tested, and is still runnable from a shell. Three layers make sure it doesn't run on a schedule:

1. **No active cron line on the VPS.** The `30 3 * * * docker exec helpmate-api python -m backend.nightly_eval ...` line is commented out in the crontab, with a documentation block in-place explaining the recommended Mon+Thu re-enable schedule and the cost trade-offs. `crontab -e` shows the commented line so the operator doesn't need to consult DEVLOG to find it.
2. **Env-gated Sentry Crons monitor.** `_start_sentry_checkin()` reads `HELPMATE_NIGHTLY_EVAL_MONITOR_ENABLED` (default off). When off, no Sentry init, no Crons check-in, no heartbeat expectation registered. Manual runs from a shell stay silent on the dashboard. When the operator flips the env to `true`, Sentry's `helpmate-nightly-eval` monitor auto-recreates on first check-in with the documented Mon+Thu schedule.
3. **Stale Sentry monitor deleted.** The original monitor object (created by the Day 35 first check-in) was deleted via Sentry's REST API so that the brief gap between "cron installed" → "cron disabled" doesn't leave a perpetual "missed heartbeat" alert state.

### Manual run pattern

For ad-hoc spot-checks (e.g. after shipping a meaningful model or prompt change):

```bash
ssh -i ~/.ssh/helpmate_ovh_vps -p 46061 ubuntu@vps-f10b021e.vps.ovh.net \
  "docker exec helpmate-api python -m backend.nightly_eval --output /tmp/eval.json && \
   docker exec helpmate-api cat /tmp/eval.json | jq .metrics"
```

Returns the headline metrics dict (RAGAS faithfulness, RAGAS answer_relevancy, FinanceBench supported_rate, final_eval supported_rate). Cost per spot-check: ~\$1.5-3. Use it whenever a model change or a prompt change lands that could plausibly move quality.

### Re-enable path (three-step flip when revenue justifies)

1. **Capture baselines from one manual run.** Run the manual pattern above, save `/tmp/eval.json`'s `.metrics` dict to `data/nightly_eval/baselines.json`.
2. **Uncomment the cron line.** The documentation block in the crontab includes the recommended Mon+Thu line. Move it out of comments.
3. **Set the env var.** Add `HELPMATE_NIGHTLY_EVAL_MONITOR_ENABLED=true` to the VPS `.env` and recreate the container (`docker compose up -d --force-recreate api`).

The Sentry monitor auto-creates on the first check-in with the schedule already wired in code (`30 3 * * 1,4` Mon+Thu).

## Consequences

### Positive

- **~\$45-90/month saved at pre-revenue stage.** That's three to six months of total budget for an MVP that hasn't shipped its first paid plan yet.
- **No Sentry alert pollution.** Without the gate, the absent daily heartbeat fires a "Cron failure" issue every day, drowning real alerts. The gate makes the absence of monitoring explicit (env off = no monitor) instead of accidental (env on but no cron = spam).
- **Manual runs still work.** The script is fully tested + runnable. The operator can spot-check whenever a model upgrade lands without the cost of a daily commitment.
- **Re-enable is trivial.** Three edits (cron + env + container restart). No code change, no migration, no Sentry monitor recreation step. The script's `monitor_config.schedule = "30 3 * * 1,4"` already matches the documented re-enable cron.

### Negative

- **Lose continuous drift detection.** A model regression that lands between manual runs will be invisible until the next manual run. Mitigation: model upgrades + prompt changes are infrequent enough at MVP stage that "after each change" coverage is comparable to daily coverage.
- **Operational knowledge required.** The operator has to remember to run the eval after meaningful changes. Mitigation: the crontab documentation block + DEVLOG Day 36 + this ADR all flag the pattern.
- **The baseline drifts unnoticed.** Without scheduled runs, there's no comparison baseline accumulated over time. Mitigation: the first re-enabled run captures the baseline from the current production state, so the "before vs after" timeline only starts when re-enabled. We don't get a free historical view from past months.

### Neutral

- **Mon+Thu is the recommended re-enable schedule, not daily.** Even when revenue justifies turning monitoring back on, the documented schedule cuts cost in half (~\$15-26/mo) by catching drift within 3-4 days rather than 24h. The cost-benefit analysis doesn't snap to "daily" the moment monitoring resumes.

## Alternatives considered

- **Daily forever (don't change anything).** Costs ~\$45-90/mo at pre-revenue. Rejected on cost.
- **Weekly only.** Single Sunday-3:30 UTC run, ~\$6-12/mo. Considered but rejected: a single weekly data point makes statistical noise hard to distinguish from real regression, and the "regression caught within 7 days" window is uncomfortably wide for a model-upgrade window that often resolves within 48-72h. Mon+Thu (2x/week) gives 3-4 day detection at a modest premium over weekly.
- **CI-triggered eval on model-config changes.** Detect when `OPENAI_MODEL_DEFAULT` or similar env-derived constants change in PRs + auto-run eval. Theoretical but brittle: most regressions come from OpenAI rotating snapshots silently, not from our env config changing, so the CI trigger would catch the wrong class of change.
- **Disable the Sentry Crons monitor but leave the cron running.** Cron generates the data, monitor doesn't alert. Loses the alert signal entirely + keeps spending money. Worst of both worlds. Rejected.
- **Delete `backend/nightly_eval.py` and start fresh later.** Throws away the existing tested script + the docs/cron entry. Re-enabling becomes a multi-day rebuild instead of three edits. Rejected — keeping the safety net cocked but unloaded is cheaper than disassembling it.

## References

- DEVLOG Day 36: "Doc Hygiene + Operational Recovery + Cost-Aware Eval Pacing"
- `backend/nightly_eval.py` — `_start_sentry_checkin()` with env-var gate
- `.env.example` — `HELPMATE_NIGHTLY_EVAL_MONITOR_ENABLED=false` with rationale comment
- VPS crontab — disabled cron line + in-place documentation block
- ADR-018: Observability stack — the Sentry Crons setup this gate sits on top of
