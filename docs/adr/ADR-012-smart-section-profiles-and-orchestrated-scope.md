# ADR-012: Smart Section Profiles And Orchestrated Scope

Date: 2026-04-27

Status: Experimental branch

## Context

Questions such as "what were the summary/conclusions from the implementation chapter?" exposed a retrieval failure mode: lexical and semantic retrieval could latch onto globally strong conclusion or methodology sections while skipping the explicitly requested local chapter. Earlier deterministic scope extraction helped some phrasing, but it risked becoming document-shaped and brittle.

The retrieval layer already had useful bounded execution primitives: `target_region_ids`, `hard_region`, synopsis-first planning, and chunk filtering. What it lacked was a document-aware interpreter that could decide when a user's wording means "stay inside this part of the document."

## Decision

Add a smart indexing layer that enriches sections with generic document profile metadata:

- section ordinal and depth
- document section role
- chapter number and title when structurally inferable
- page ranges
- scope labels and aliases

This branch also selectively carries forward the useful part of the older hybrid-indexing candidate branch: policy documents remain eligible for semantic indexing when structure or synopsis quality is weak. The indexer recognizes policy-native concepts such as coverage, benefits, exclusions, claims, waiting periods, eligibility, renewal, definitions, and schedule-of-benefits sections. It does not merge the older branch wholesale because that branch also contained stale prompt/version rollbacks that conflict with newer support-guardrail work.

Then add an LLM retrieval orchestrator before the older structured planner. The orchestrator receives the user question and a compact document map, returns strict JSON, and chooses existing section IDs when a question is locally scoped.

The deterministic code does not try to understand every phrasing. It validates and enforces:

- returned section IDs must exist in the document map
- low-confidence orchestrator output is ignored
- hard scoped plans disable global fallback
- hard scoped final evidence is filtered to the allowed section IDs

The evidence selector remains a separate post-retrieval evidence judge, but it now receives the orchestration context. This gives it the same scope, route, and answer-focus metadata without merging planner and selector responsibilities.

## Consequences

This keeps intelligence at the interpretation boundary while preserving deterministic safety at the retrieval boundary. It should reduce cases where answer-focus words like "summary", "conclusion", or "findings" accidentally pull evidence from the global conclusion when the user asked for a specific chapter or section.

The tradeoff is one extra lightweight LLM call for retrieval orchestration when enabled. The current implementation defaults to `HELPMATE_RETRIEVAL_ORCHESTRATOR_ENABLED=true` on this experimental branch, with a confidence floor of `0.55` and a document-map cap of `120` sections.

The selector now uses the retrieval plan context as a second-stage cue, not as a second planner. In hard scoped summary questions, it can prefer chapter overview/summary chunks over detailed subsections when the evidence scores are otherwise close. In broad questions, `scope_strictness=none` tells the selector not to collapse the answer into one local chapter.

## Validation

Unit coverage now verifies:

- section profiles infer generic chapter scope and aliases
- orchestrator hard scope keeps only valid section IDs
- invented section IDs are ignored
- final hard-scope evidence compliance removes out-of-scope candidates
- policy canonical headings and aliases are recognized during section building
- coarse policy documents can trigger indexing-time repair
- policy synopsis refinement is gated by structure/synopsis quality rather than blanket-skipped

Retrieval eval coverage now includes `scoped_retrieval_eval`, a retrieval-only scope benchmark for chapter-scoped summary questions. After filtering low-value front matter from orchestrated hard scopes, the 2026-04-27 scoped run showed:

- orchestrator off: page-hit `1.00`, chapter-scope hit `1.00`, full scope compliance `0.00`, scope precision `0.4375`
- orchestrator on: page-hit `0.75`, chapter-scope hit `1.00`, full scope compliance `1.00`, scope precision `1.00`

The page-hit dip is a stricter page-label artifact after table-of-contents/front-matter chunks were removed from hard scope. The important production signal is that every orchestrated candidate remains inside the requested chapter, while front-matter false scope is suppressed.

A lighter thesis factual retrieval regression also showed neutral-to-positive page retrieval impact:

- top-k page hit remained `0.5833`
- mean reciprocal rank moved from `0.3958` to `0.4583`
- multi-region recall moved from `0.20` to `0.25`

The full-stack snapshot also completed on 2026-04-27:

- retrieval objective score `0.6274` across `76` positive questions
- answer supported rate `0.8158` across `76` positive questions
- answer citation page-hit rate `0.6579`
- RAGAS faithfulness mean `0.8173`
- RAGAS context precision mean `0.6560`

The targeted lean `ragas` upgrade suite then checked the newest behavior against `main` and external retrieval baselines:

- current branch on six targeted cases: supported rate `1.0000`, faithfulness `0.9050`, answer relevancy `0.6034`, context precision `0.7500`
- five-case regression versus `main`: faithfulness `+0.0110`, answer relevancy `+0.0966`, context precision `+0.1000`, with supported rate unchanged at `1.0000`
- same six-case vendor comparison: Helpmate `6/6` supported answers versus `4/6` for OpenAI File Search and `4/6` for Vectara
- on the implementation-chapter thesis case, the branch stayed inside Chapter 4 pages while `main` drifted into conclusion, methodology, and results pages

Full test suite result on this branch after the front-matter scope fix: `116 passed`.
