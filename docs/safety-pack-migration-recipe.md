# Production Safety Pack — post-merge wiring recipe

The Production Safety Pack lands three improvements:

1. Nightly evaluation cron (`backend/nightly_eval.py` + ops doc cron entry)
2. Schema-strict output validation (`backend/openai_service.py` + `backend/schemas.py`)
3. Per-query cost tracking in `helpmate_run_traces` (SQL migration + cost computation)

Items 1 and 2 (wrapper + schemas) ship complete in this branch. Items 2 (call-site migrations) and 3 (trace-store write path + SQL apply) require touching files under `src/` and the Supabase production database — both of which were outside the writable scope of the implementation session. This file is the recipe for finishing them post-merge.

The new code is structurally complete and unit-tested. The "wiring up" is mechanical: imports + a thin replacement of existing direct OpenAI calls with the new wrapper.

## 1. Move `backend/openai_service.py` and `backend/schemas.py` into `src/` ✅ DONE

The files now live at:
  * `src/openai_service.py` (was `backend/openai_service.py`)
  * `src/schemas_llm_outputs.py` (was `backend/schemas.py` — renamed at move time to mirror AI Job Agent's naming and keep Pydantic models in their own file rather than mixing with the existing dataclasses in `src/schemas.py`)

All imports in `tests/test_structured_outputs.py` and `tests/test_cost_tracking.py` are updated. 61 tests still pass. Subsequent steps below use the new `src/` paths.

## 2. Migrate `src/generation/service.py` to the schema-strict path

The existing `AnswerGenerator.generate(...)` method calls `self.client.chat.completions.create(...)` with `response_format={"type": "json_object"}` and then `json.loads`. Replace the OpenAI call with a `run_structured_prompt` call that validates through `AnswerOutput`.

**Add to `AnswerGenerator.__init__`:**

```python
from src.openai_service import OpenAIService, CostCollector
# ...
self.cost_collector = CostCollector()
self._openai_service = OpenAIService(
    settings,
    cost_recorder=self.cost_collector,
    client=self.client,  # reuse the existing OpenAI client instance
)
```

**Replace the `generate(...)` model-call block:**

```python
# Before:
response = self.client.chat.completions.create(
    model=model_name,
    messages=[
        {"role": "system", "content": "You answer questions using only supplied document evidence."},
        {"role": "user", "content": prompt},
    ],
    response_format={"type": "json_object"},
    temperature=0,
)
content = response.choices[0].message.content or "{}"
# ...
payload = json.loads(content)

# After:
from src.openai_service import StructuredOutputError
from src.schemas import AnswerOutput

try:
    output = self._openai_service.run_structured_prompt(
        system="You answer questions using only supplied document evidence.",
        user=prompt,
        task_name="answer_generation",
        model=model_name,
        response_model=AnswerOutput,
    )
    payload = output.model_dump()
except StructuredOutputError as exc:
    logger.warning("Schema-strict answer generation failed (%s); falling back.", exc)
    answer = self._fallback_answer(question, evidence)
    # ... existing fallback wiring
    return answer
```

**Replace the `verify_support_status(...)` block** the same way, using `SupportStatusVerifierOutput` and `task_name="support_status_verifier"`.

**Replace the `verify_supported_answer(...)` block** using `SupportVerifierOutput` and `task_name="support_verifier"`.

## 3. Migrate `src/query_router.py` to the schema-strict path

The `_llm_route(...)` method has the same structure: direct OpenAI call → JSON parse → validate. Replace with:

```python
from src.openai_service import StructuredOutputError
from src.schemas import QueryRouterOutput

# In QueryRouter.__init__, after the existing client setup:
from src.openai_service import OpenAIService
self.cost_collector = CostCollector()
self._openai_service = OpenAIService(settings, cost_recorder=self.cost_collector, client=self.client)

# In _llm_route, replace the direct create call:
try:
    output = self._openai_service.run_structured_prompt(
        system="You classify retrieval routes for a document QA pipeline.",
        user=prompt,
        task_name="query_router",
        model=self.settings.router_model,
        response_model=QueryRouterOutput,
    )
except StructuredOutputError:
    return current  # keep the heuristic decision
if output.route not in {"chunk_first", "section_first", "hybrid_both", "synopsis_first"}:
    return current
return RoutingDecision(
    route=output.route,
    confidence=0.72,
    reasons=[*current.reasons, output.reason or "LLM fallback refined the retrieval route."],
    source="llm_fallback",
)
```

## 4. Wire `CostCollector` into the run trace

`src/pipeline/service.py::HelpmatePipeline._build_run_trace(...)` currently builds the payload but knows nothing about token usage. After Step 2 above, the `AnswerGenerator` and `QueryRouter` each own a `CostCollector`. Fold them into the trace.

**Option A (simpler):** Promote the collector to a pipeline-scoped object so a single collector captures every LLM call across the request.

```python
# In HelpmatePipeline.__init__:
from src.openai_service import CostCollector
self.cost_collector = CostCollector()
# Pass the same collector into the generator + router constructors instead
# of letting each subsystem build its own.
```

**Option B (looser coupling):** Each subsystem owns its own collector; the pipeline aggregates them by merging `records` before building the trace.

**Inside `_build_run_trace`:**

```python
totals = self.cost_collector.totals()
payload["llm_calls"] = self.cost_collector.to_payload()
# Reset for the next request:
self.cost_collector.records.clear()

return RunTraceRecord(
    # ... existing fields ...
    prompt_tokens=totals["prompt_tokens"],
    completion_tokens=totals["completion_tokens"],
    cost_usd=totals["cost_usd"],
    model_name=totals["model_name"],
    payload=payload,
)
```

That requires extending `RunTraceRecord` in `src/schemas.py`:

```python
@dataclass
class RunTraceRecord:
    # ... existing fields ...
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    model_name: str = ""
```

## 5. Update `src/traces/store.py` to write the cost columns

`SupabaseRunTraceStore.save_trace(...)` currently upserts `(trace_id, document_id, fingerprint, question, created_at, expires_at, payload)`. Add the new columns:

```python
def save_trace(self, trace: RunTraceRecord) -> None:
    payload = {
        "trace_id": trace.trace_id,
        "document_id": trace.document_id,
        "fingerprint": trace.fingerprint,
        "question": trace.question,
        "created_at": trace.created_at,
        "expires_at": trace.expires_at,
        "payload": trace.to_dict(),
        "prompt_tokens": trace.prompt_tokens,
        "completion_tokens": trace.completion_tokens,
        "cost_usd": trace.cost_usd,
        "model_name": trace.model_name,
    }
    # ... existing upsert ...
```

The local trace store needs no changes — `RunTraceRecord.to_dict()` already covers serialization to JSON on disk.

## 6. Apply the Supabase migration

Run `docs/supabase-run-traces-cost-columns.sql` in the Supabase SQL editor (or via the Supabase MCP if available to the operator). The migration:

- adds `prompt_tokens int`, `completion_tokens int`, `cost_usd numeric(10,6)`, `model_name text` (all `not null default 0/''`)
- adds two indexes (cost-margin rollups, cost-per-model rollups)
- inherits existing RLS policies

Existing rows backfill with the defaults — no migration of historical data needed.

## 7. Smoke-test

After the merge, the test suite should still pass cleanly:

```bash
uv run pytest tests/test_nightly_eval.py tests/test_structured_outputs.py tests/test_cost_tracking.py
```

The integration smoke test for `/qa`:

```bash
curl -X POST https://api.helpmateai.xyz/qa \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"document_id": "...", "question": "..."}'
# Then verify the new trace row has populated cost columns:
psql $SUPABASE_URL -c "select trace_id, prompt_tokens, completion_tokens, cost_usd, model_name from helpmate_run_traces order by created_at desc limit 5;"
```

## Items deliberately deferred

- **Planner LLM (`src/retrieval/planner.py`)** — also calls `chat.completions.create`, but the prompt is richer and the schema would be larger. Migrating it is the same pattern; deferred to keep the safety-pack scope tight.
- **Structure repair / landmarks / chunk semantics / synopsis semantics** — each has its own OpenAI call with a bespoke prompt. Lower-frequency calls, less user-visible blast radius. Same migration pattern when revisiting.
- **OpenAI client unification** — long-term, every subsystem should accept an `OpenAIService` instance via DI rather than building its own client. That's a bigger refactor and not part of the safety pack.
