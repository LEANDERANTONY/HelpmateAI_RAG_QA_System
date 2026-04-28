# HelpmateAI — Outstanding Experiment Plan
*Context document for running validation experiments on untested architecture defaults*

---

## Background and Purpose

This document is a prioritised experiment plan for the HelpmateAI RAG system. It exists because the project has a clear hierarchy of what is benchmark-backed versus what is engineering intuition. The goal is to close the gaps systematically so every major architectural default can be defended with evidence rather than reasoning alone.

This document should be read alongside the existing ADRs in `docs/adr/` and the eval roadmap in `docs/evals/EVAL_ROADMAP.md`.

---

## Strategic Update — Indexing Semantics Is Now The Main Frontier

The planner and selector story is no longer the clearest bottleneck in the stack. The newer hard-case traces suggest that the next meaningful quality gains will come from improving the semantic structure that indexing hands to retrieval, reranking, and planning.

Why this matters now:

- the graduate-project failure trace showed that the planner could identify the right broad section (`EXPERIMENTAL RESULTS`) while retrieval still surfaced heading stubs, table-of-contents text, and reference-like chunks instead of the real body-evidence chunk
- the `reportgeneration2` repair follow-up showed that the remaining gap is heuristic coverage, not threshold calibration
- the latest full-stack snapshot with the structured LLM planner improved retrieval and supported answers overall, but still gave back grounding quality on focused `ragas`

The implication is:

- planner intelligence alone is not enough
- later retrieval quality is still bounded by the section maps, chunk metadata, synopses, and low-value detection produced at indexing time
- the next architecture sprint should focus on a hybrid indexing redesign rather than another planner-only iteration

### New architectural direction

The indexing stack should move toward a three-tier design:

1. `Deterministic backbone`
   - extraction, page diagnostics, heading and clause signals, chunk creation, and basic topology edges stay deterministic
2. `Confidence layer`
   - deterministic quality signals decide where the current structure looks trustworthy versus ambiguous
3. `Selective intelligence`
   - LLM passes are used only for the semantically heavy cases:
     - ambiguous section repair
     - low-value vs meaningful-content adjudication
     - chunk usefulness classification
     - higher-quality synopsis generation
     - semantic relabeling of weak sections

This keeps the indexing system cheap and reproducible where mechanical rules work well, while still allowing human-like judgment where the current heuristic layer is clearly weak.

### Strategic correction after the first indexing sprint

The first deterministic indexing sprint was useful diagnostically, but it also exposed a design risk: if Layer 1 becomes too opinionated, it starts to overfit to the exact failure traces we are trying to fix.

What we learned:

- deterministic indexing improvements can help the weak report-family documents
- but the same rule-heavy narrowing can also distort documents that were already healthy
- the right role for deterministic indexing is to measure and annotate, not to become the primary semantic judge

So the corrected architecture principle is now:

1. `Layer 1 measures`
   - deterministic indexing should emit signals and light priors
2. `Layer 2 interprets`
   - selective semantic LLM passes should do the hard understanding work
3. `Layer 3 decides`
   - benchmark-backed gating should decide when Layer 2 is worth paying for

This means the redesign should now be treated as:

- `Layer 1`: conservative metadata backbone
- `Layer 2`: primary semantic indexing layer
- `Layer 3`: calibration and escalation logic

not:

- `Layer 1`: aggressive deterministic narrowing

### Revised target design

#### Layer 1 â€” light deterministic signals only

Layer 1 should keep producing signals such as:

- `heading_like_score`
- `front_matter_score`
- `repeated_header_score`
- `body_density_score`
- `table_fragment_score`
- `section_boundary_confidence`
- `canonical_heading_match`
- `title_noise_score`
- `chunk_role_prior`
- `body_evidence_score`

But Layer 1 should **not** become a heavy semantic policy engine. In particular, we should avoid expanding:

- strong query-conditioned suppression rules
- aggressive front-matter penalties beyond obvious low-value pages
- brittle deterministic continuation rescue logic as the main fix path
- over-opinionated section and region shaping by heuristics alone

Layer 1 should be treated as a feature extractor for later layers.

#### Layer 2 â€” semantic adjudication becomes the main improvement layer

This should now be the core indexing-quality layer.

Layer 2 should be split into three selective intelligent passes:

1. `Section adjudicator`
   - repair ambiguous boundaries
   - clean noisy pseudo-sections
   - relabel weak `general` sections into meaningful section kinds
2. `Chunk usefulness adjudicator`
   - classify ambiguous chunks as:
     - `body_evidence`
     - `heading_stub`
     - `navigation_noise`
     - `reference_noise`
     - `summary_evidence`
     - `table_fragment`
3. `Synopsis writer`
   - produce stronger synopsis text for long, noisy, or semantically important sections

This keeps semantic interpretation where it belongs: in the selective intelligent layer, not in hardcoded retrieval penalties.

#### Layer 3 â€” calibrated gate, not guessed thresholds

Layer 3 should eventually decide among:

- deterministic-only indexing
- section adjudication only
- section + chunk adjudication
- section + chunk + synopsis intelligence

But that decision should be benchmark-backed. We should not hardcode a final gate by intuition alone.

The right calibration method remains:

1. run the intelligent sublayer `always-on` on the weak-family slice
2. measure exactly where it helps
3. compare those gains against deterministic confidence features
4. only then promote a gate to production

### Concrete implementation shape

#### Modules to keep mostly deterministic

- `src/sections/service.py`
- `src/chunking/service.py`
- `src/topology/service.py`

These should focus on:

- extraction and page diagnostics
- initial section proposals
- chunk slicing
- structural metadata
- basic topology edges

#### Modules to strengthen semantically

- `src/sections/repair.py`
  - evolve from coarse document-level repair toward selective section adjudication
- new candidate module: `src/chunking/chunk_semantics.py`
  - chunk usefulness adjudication for suspicious chunks only
- new candidate module: `src/topology/synopsis_semantics.py`
  - synopsis rewriting for weak or strategically important sections

#### Retrieval integration target

`src/retrieval/hybrid.py` should eventually consume:

- light deterministic priors from Layer 1
- semantic section labels from Layer 2
- chunk usefulness labels from Layer 2
- higher-quality synopses from Layer 2

without relying mainly on large new deterministic penalty trees.

---

## Current State — What Is and Is Not Benchmark-Backed

### Already validated with evidence

| Component | How it was validated |
|---|---|
| Chunking default `1200 / 240` | Full retrieval sweep, answer layer ablation, focused RAGAS cross-check |
| Reranker model `cross-encoder/ms-marco-MiniLM-L6-v2` | Model sweep across TinyBERT-L2, MiniLM-L6, MiniLM-L12 on retrieval, answer layer, RAGAS |
| Reranker on vs off | Full ablation — reranker clearly justified |
| Planner thresholds `0.70 / 0.62` | 9x8 threshold pair sweep, calibrated before planner ablation |
| Planner on vs off | Ablation run after calibration — small positive, kept |
| Selector weights `0.25 / 0.75` | Weight sweep 0.0 to 1.0 in 0.05 steps under prune mode |
| Selector prune vs reorder-only | New controlled experiment — reorder-only is decisively better |

### NOT yet validated — engineering defaults only

| Component | Current default | Why it matters |
|---|---|---|
| Selector trigger gap threshold | `0.08` | Controls what % of queries activate the selector |
| Selector spread trigger conditions | `global` and `sectional` always trigger | Never isolated — may be too broad or too narrow |
| Selector always-on vs conditional | Conditional | Never compared always-on reorder-only against conditional |
| Selector weights under reorder-only mode | `0.25 / 0.75` (validated under prune mode only) | Weight stakes are different when no pruning occurs |
| Synopsis section window | `4` | Directly controls how many sections are expanded in synopsis-first retrieval |
| Synopsis dense/lexical top_k | `8 / 8` | Input pool size for synopsis ranking |
| Synopsis fused top_k | `5` | How many synopses are considered before chunk retrieval |
| Global fallback top_k | `4` | Extra evidence pool size for broad paper-summary questions |
| Planner candidate region limit | `6` | Max region IDs the planner considers |
| Structure repair confidence threshold | `0.62` | When to trigger LLM structure repair at indexing time |
| Structure repair confidence signal weights | Hardcoded penalties | Never ablated individually |
| Topology edge types in scope expansion | Combined set per constraint mode | Never isolated by edge type |

---

## Key Recent Finding — Selector Reorder-Only Mode

### What was discovered

The original ablation that led to disabling the selector (`evidence_selector_enabled = False`) was conflating two separate effects: chunk pruning and chunk reordering. The selector was reducing the candidate set to `max_evidence = 2` before answer generation. When a supporting chunk was dropped, the generator occasionally wrote claims that went beyond what the smaller evidence set strictly supported, causing RAGAS faithfulness to drop.

A controlled experiment with three clean conditions produced the following results:

| Mode | Faithfulness | Answer Relevancy | Context Precision | Supported Rate |
|---|---|---|---|---|
| Selector off (planner + reranker) | 0.9310 | 0.6555 | 0.9036 | 0.8421 |
| Selector prune (current disabled default) | 0.9174 | 0.6015 | 0.9158 | 0.8289 |
| Selector reorder-only (new mode) | **0.9657** | 0.6436 | **0.9608** | **0.8553** |

Reorder-only mode produced the highest faithfulness and context precision in the entire project history.

### What changed in the code for this experiment

- Selector now has a `reorder_only` mode via an `evidence_selector_prune=False` flag
- When pruning is off, the selector reorders the top selector-reviewed chunks first then appends remaining retrieved chunks in their original order
- This isolates smarter ordering from loss of context

### Current status

The selector should be re-enabled in reorder-only mode. The trigger conditions and weight blend under this new mode still need validation before a new ADR is written.

---

## Priority 0 — Hybrid Indexing Semantics Redesign

**This is now the most important architecture-design follow-up. The goal is not to make the entire indexing pipeline LLM-driven. The goal is to keep the mechanical backbone deterministic and add intelligence only where semantic understanding is load-bearing.**

### Known failure signatures this redesign is meant to address

1. **Graduate Project results-summary failure**
   - the planner could reach the correct broad region
   - but the candidate pool still elevated:
     - heading-only result stubs
     - `CONTENTS` chunks
     - reference-like text
   - the actual body-evidence chunk containing the performance trend text never entered the inspected top-20 direct chunk candidates

2. **`reportgeneration2` structure-repair gap**
   - the repair-threshold sweep showed this is not a threshold problem
   - the remaining weakness is heuristic coverage in the current deterministic repair signals

3. **Broad academic / report front-matter pollution**
   - unseen-document work showed that noisy front matter and weak section headings are still capable of polluting retrieval paths on academic and project-style PDFs

### Proposed indexing design

#### Tier A — Deterministic backbone

These should remain deterministic:

- raw extraction and page boundaries
- page-level diagnostics such as:
  - repeated header/footer patterns
  - front-matter likelihood
  - table-of-contents likelihood
  - reference-page likelihood
  - content density
  - numeric/table density
- initial section proposal
- chunk slicing and overlap
- explicit metadata extraction:
  - page labels
  - clause/page references
  - section paths
- topology primitives:
  - `previous_next`
  - `parent_child`
  - `same_region_family`
  - `semantic_neighbor`

The deterministic backbone should also emit richer metadata than it does today.

New chunk- and section-level metadata candidates:

- `section_boundary_confidence`
- `section_title_confidence`
- `section_noise_score`
- `front_matter_score`
- `low_value_prior`
- `chunk_role_prior`
  - `body`
  - `heading_stub`
  - `table_fragment`
  - `reference_like`
  - `navigation_like`
- `body_evidence_score`
- `heading_only_flag`
- `continuation_chunk_id`

#### Tier B — Confidence layer

This is the bridge between deterministic indexing and selective LLM help.

The confidence layer should answer:

- does this document need structure repair beyond the current heuristic thresholding?
- is this section semantically labeled with high enough confidence?
- is this chunk likely meaningful evidence or just structural noise?
- is the deterministic synopsis likely good enough for routing and synopsis-first retrieval?

Target confidence outputs:

- document-level:
  - `structure_confidence`
  - `front_matter_pollution_score`
  - `section_map_health`
- section-level:
  - `semantic_label_confidence`
  - `low_value_confidence`
  - `synopsis_quality_prior`
- chunk-level:
  - `chunk_usefulness_confidence`
  - `body_evidence_confidence`

#### Tier C — Selective intelligence

Only low-confidence or semantically ambiguous items should go here.

High-value selective LLM passes:

1. `Section adjudicator`
   - refine ambiguous boundaries
   - suppress publisher/front-matter pseudo-sections
   - relabel weak sections such as `general` into stronger semantic roles where justified

2. `Chunk usefulness adjudicator`
   - classify suspicious chunks as:
     - `heading_stub`
     - `body_evidence`
     - `summary_evidence`
     - `table_fragment`
     - `navigation_noise`
     - `reference_noise`

3. `Synopsis writer`
   - rewrite weak deterministic synopses for long, noisy, or semantically important sections

4. `Semantic region relabeler`
   - resolve weak `section_kind` / `region_kind` assignments only when deterministic confidence is poor

### Proposed execution order

#### Experiment 0A — Deterministic chunk-usefulness and low-value metadata

**Why:** This is the most direct response to the graduate-project failure. Before introducing any new LLM pass, we should strengthen the chunk and page metadata so retrieval can penalize heading-only, TOC, and reference-like chunks more intelligently.

**What to implement:**

- richer page diagnostics in `src/sections/service.py`
- richer chunk diagnostics in `src/chunking/service.py`
- retrieval-time penalties and boosts in `src/retrieval/hybrid.py` using the new metadata

**What to measure:**

- local retrieval objective on the main benchmark
- focused reruns on:
  - `Graduate Project.pdf`
  - `reportgeneration`
  - `reportgeneration2`

**Main question:** Can deterministic evidence-quality signals alone recover the hard-case body chunks that the current stack misses?

#### Experiment 0B — Selective synopsis intelligence

**Why:** The planner and synopsis-first paths are only as good as the synopses they inherit. Weak section summaries make broad methodology/results questions much harder than they need to be.

**What to implement:**

- deterministic synopsis quality priors in `src/topology/service.py`
- LLM rewrite only for low-confidence synopses

**What to measure:**

- plan accuracy
- section hit / region hit
- broad-summary question performance on thesis and report-like documents

#### Experiment 0C — Selective section semantic adjudication

**Why:** The current repair layer is document-level and coarse. The remaining weak cases are more about semantic mislabeling and noisy pseudo-sections than about pure threshold calibration.

**What to implement:**

- extend `src/sections/repair.py` from thresholded document repair toward selective section adjudication
- preserve deterministic sections where confidence is already high

**What to measure:**

- retrieval objective on the main corpus
- targeted improvement on `reportgeneration2`
- regression checks on healthy documents such as:
  - health policy
  - thesis

#### Experiment 0D — Confidence-gate calibration

**Why:** Even the handoff from deterministic indexing to selective intelligence must be benchmark-backed. We should not hardcode a confidence threshold by intuition.

**Method:**

1. run each intelligent sublayer in an `always-on oracle` mode on the hard-doc slice
2. measure where it truly helps
3. compare those improvements against the deterministic confidence signals
4. only then choose gating thresholds

**Deliverable:** benchmark-backed escalation logic for indexing-time LLM help

### Expected impact by document family

- `Graduate Project.pdf`
  - strong expected gain from deterministic chunk-usefulness metadata
  - additional gain likely from better synopsis quality and low-value/front-matter suppression
- `reportgeneration2`
  - likely needs the selective intelligence layer, especially section adjudication
- thesis and other academic long-form PDFs
  - moderate expected gain from cleaner synopses and stronger low-value suppression
- policy documents
  - low expected gain, but important regression guardrail because these are already stable

### Decision rule

The redesign should only be promoted in layers.

- first promote deterministic metadata improvements if they help on the hard-doc slice without hurting the stable policy/thesis baselines
- then promote selective intelligent indexing only where always-on oracle runs demonstrate real value
- only after that calibrate confidence gates for production

### Priority 0 revision â€” lighter deterministic indexing, stronger semantic Layer 2

The notes above capture the first pass of the indexing redesign. After the first implementation sprint, the direction is now refined:

- `Layer 1` should stay light and mostly diagnostic
- `Layer 2` should become the main semantic improvement layer
- `Layer 3` should become the calibrated escalation gate

This revision supersedes any interpretation that Layer 1 should keep accumulating stronger deterministic retrieval penalties.

#### Revised Layer 1 role

Layer 1 should keep emitting signals such as:

- `heading_like_score`
- `front_matter_score`
- `repeated_header_score`
- `body_density_score`
- `table_fragment_score`
- `section_boundary_confidence`
- `canonical_heading_match`
- `title_noise_score`
- `chunk_role_prior`
- `body_evidence_score`

But Layer 1 should **not** keep expanding into:

- strong query-conditioned suppression rules
- aggressive front-matter penalties beyond obvious low-value pages
- brittle deterministic continuation rescue as the main fix path
- over-opinionated section and region shaping by heuristics alone

The right mental model is:

- Layer 1 measures
- Layer 2 interprets
- Layer 3 decides when interpretation is worth paying for

#### Revised Layer 2 role

Layer 2 should now be treated as the main indexing-quality layer and split into three selective semantic passes:

1. `Section adjudicator`
   - repair ambiguous boundaries
   - suppress noisy pseudo-sections
   - relabel weak `general` sections into meaningful semantic roles
2. `Chunk usefulness adjudicator`
   - classify suspicious chunks as:
     - `body_evidence`
     - `heading_stub`
     - `navigation_noise`
     - `reference_noise`
     - `summary_evidence`
     - `table_fragment`
3. `Synopsis writer`
   - produce stronger synopsis text for long, noisy, or semantically important sections

Candidate implementation modules:

- extend `src/sections/repair.py`
- add `src/chunking/chunk_semantics.py`
- add `src/topology/synopsis_semantics.py`

#### Revised Layer 3 role

Layer 3 should eventually decide among:

- deterministic-only indexing
- section adjudication only
- section + chunk adjudication
- section + chunk + synopsis intelligence

But the gate should be benchmark-backed, not guessed. The calibration order should be:

1. run the semantic layer `always-on` on the weak-family slice
2. measure where it really helps
3. compare those gains against deterministic confidence signals
4. only then promote a production gate

#### Revised execution order

1. `0A` simplify Layer 1 into a light metadata backbone
2. `0B` selective section adjudication
3. `0C` chunk usefulness adjudication
4. `0D` selective synopsis intelligence
5. `0E` confidence-gate calibration

#### Revised benchmark order

Until this redesign stabilizes, keep testing narrow:

- weak-family retrieval:
  - `Graduate Project.pdf`
  - `reportgeneration`
  - `reportgeneration2`
- guardrails:
  - health policy
  - thesis

Only after that should we run:

- targeted full-flow answer checks
- then broader suite reruns

---

## Priority 1 — Complete the Selector Story

**These are the most urgent experiments. The selector finding is the strongest recent result in the project and needs a complete evidence trail before ADR-010 can be written.**

---

### Experiment 1A — Re-run Selector Weight Sweep Under Reorder-Only Mode

**Why:** The existing weight sweep (`evidence_selector_weight_sweep.py`) was run under prune mode. In prune mode, weights determine which chunks survive elimination. In reorder-only mode, weights only determine ordering. The decision stakes are different and the optimal blend may have shifted.

**What to run:**

```bash
# Modify the weight sweep to run with evidence_selector_prune=False
# Then run the sweep across the same range as before
uv run python -m src.evals.evidence_selector_weight_sweep --step 0.05
```

**What to vary:** `rank_weight` from 0.0 to 1.0 in 0.05 steps, `llm_weight = 1 - rank_weight`

**What to measure:** Same objective as before: `0.45 * page_hit_rate + 0.35 * fragment_recall + 0.20 * MRR`

**What to look for:**
- Does the broad plateau still appear around `rank_weight = 0.25`?
- Does the optimal region shift toward more or less LLM weight under reorder-only mode?
- Is the plateau still broad or does reorder-only create a sharper optimum?

**Expected outcome:** Plateau probably holds but now the 0.25/0.75 split is validated under the correct mode.

**Deliverable:** Updated weight default confirmed or adjusted, documented as part of ADR-010.

---

### Experiment 1B — Gap Threshold Sweep

**Why:** `evidence_selector_gap_threshold = 0.08` controls whether the top two candidates are "close enough" to warrant the selector running. This value was set by intuition and has never been swept. Too tight means the selector rarely runs and most queries miss the benefit. Too loose means it runs on queries where reranker order was already decisive and adding LLM overhead gains nothing.

**What to vary:** `evidence_selector_gap_threshold` across `0.04, 0.06, 0.08, 0.10, 0.15, 0.20, 1.0 (always-on)`

**For each threshold value measure:**
- What percentage of benchmark queries trigger the selector
- Retrieval objective (page hit rate, MRR, fragment recall)
- Answer layer supported rate and citation page-hit rate
- Spot RAGAS on top 3 threshold candidates (faithfulness, context precision)

**Key comparison:** Always-on (`1.0`) vs current `0.08` — if always-on reorder-only wins on RAGAS, the conditional logic may be unnecessary complexity.

**Implementation note:** This requires modifying `_should_select()` to accept a threshold override for sweep purposes, or running separate config variants.

**Deliverable:** Benchmark-backed gap threshold, or evidence that always-on is simply better.

---

### Experiment 1C — Spread Condition Isolation

**Why:** The selector currently also triggers unconditionally on `global` and `sectional` evidence spread, regardless of the score gap. This was a design choice based on intuition — broad questions benefit from smarter ordering. That may be correct but it was never isolated.

**Three conditions to compare:**
1. Trigger on `weak_evidence` only
2. Trigger on `global / sectional` spread only
3. Current combined logic (weak OR global/sectional OR ambiguous gap)
4. Always-on (as a reference)

**What to measure:**
- Retrieval objective per document family
- Answer layer supported rate
- Whether thesis and research paper benchmarks (where global/sectional is common) respond differently than policy benchmarks (where chunk-first atomic lookup is more common)

**Deliverable:** Validated spread trigger conditions or simplified trigger logic, documented in ADR-010.

---

### ADR-010 — Selector Story Complete

Once 1A, 1B, 1C are done, write ADR-010 covering:

- Why the selector was originally disabled (prune mode hurt faithfulness)
- The controlled experiment that isolated pruning from reordering
- Reorder-only mode results
- Weight re-validation under reorder-only mode
- Gap threshold calibration finding
- Spread condition validation
- Final recommendation: selector re-enabled in reorder-only mode with validated trigger conditions and weight blend

---

## Priority 2 — Synopsis and Topology Hyperparameters

**These affect every non-chunk-first query — all synopsis-first, global-summary-first, and hybrid-both paths. The impact surface is large, especially for thesis and research paper document families.**

---

### Experiment 2A — Synopsis Section Window Sweep

**Why:** `synopsis_section_window = 4` controls how many top-ranked synopsis sections are used to seed chunk retrieval in synopsis-first and global-summary-first paths. Too small and you miss relevant sections. Too large and you introduce noise that dilutes the evidence pool.

**What to vary:** `synopsis_section_window` across `2, 3, 4, 5, 6`

**What to measure:**
- Section hit rate and region hit rate (most sensitive to this parameter)
- Top-k page hit rate and MRR on thesis and research paper benchmarks specifically
- Retrieval objective overall

**Document families to focus on:** Thesis and pancreas benchmarks — these are the ones where synopsis-first routing is most active. Policy benchmarks use chunk-first more often so they are less sensitive to this parameter.

**Deliverable:** Benchmark-backed synopsis section window, or confirmation that 4 is the correct default.

---

### Experiment 2B — Synopsis Top-K Sweep

**Why:** `synopsis_dense_top_k = 8`, `synopsis_lexical_top_k = 8`, `synopsis_fused_top_k = 5` control the input pool for synopsis ranking. These were set as reasonable defaults and never swept.

**Grid to test:**

| synopsis_fused_top_k | synopsis_dense_top_k | synopsis_lexical_top_k |
|---|---|---|
| 3 | 6 | 6 |
| 4 | 6 | 6 |
| 5 | 8 | 8 (current) |
| 6 | 8 | 8 |
| 8 | 10 | 10 |

**What to measure:**
- Section hit rate and region hit rate
- Plan accuracy
- Retrieval objective on thesis and research paper benchmarks

**Implementation note:** Use the existing `run_retrieval_eval` infrastructure with modified settings. The grid is small enough to run without a dedicated sweep script.

**Deliverable:** Benchmark-backed synopsis top-k defaults.

---

### Experiment 2C — Global Fallback Top-K Sweep

**Why:** `global_fallback_top_k = 4` controls how many additional chunks are pulled from outside the prioritised anchor sections during global-summary retrieval. This is the safety net for broad paper-summary questions when the anchor sections don't fully cover the evidence needed.

**What to vary:** `global_fallback_top_k` across `2, 3, 4, 5, 6, 8`

**Document families to focus on:** Pancreas8 and reportgeneration — broad paper-summary questions are the hardest remaining benchmark cases and global fallback is most active there.

**What to measure:**
- Page hit rate and fragment recall on broad summary questions specifically
- Whether larger fallback pools improve or hurt context precision

**Deliverable:** Benchmark-backed fallback pool size.

---

### Experiment 2D — Planner Candidate Region Limit Sweep

**Why:** `planner_candidate_region_limit = 6` controls how many region IDs the topology service returns as candidates for the retrieval plan's `target_region_ids`. Too few and the planner misses the right region. Too many and the soft constraints become too diffuse to be useful.

**What to vary:** `planner_candidate_region_limit` across `3, 4, 5, 6, 8, 10`

**What to measure:**
- Plan accuracy
- Region hit rate
- Retrieval objective overall

**Deliverable:** Benchmark-backed region limit, or confirmation that 6 is well-calibrated.

---

## Priority 3 — Structure Repair Threshold

**Lower urgency because repair activates on a minority of documents and qualitative validation was reasonable. But the threshold and signal weights need evidence before they can be formally defended.**

---

### Experiment 3A — Repair Confidence Threshold Sweep

**Why:** `structure_repair_confidence_threshold = 0.62` determines when the LLM repair pass is triggered at indexing time. Too low and healthy documents get unnecessarily repaired, wasting LLM calls and risking regression. Too high and genuinely noisy documents like publisher-formatted journal PDFs are left with poor section maps.

**What to vary:** `structure_repair_confidence_threshold` across `0.50, 0.55, 0.62, 0.68, 0.75`

**For each threshold measure:**
- What percentage of benchmark documents trigger repair
- Retrieval quality on reportgeneration and reportgeneration2 (known-noisy documents)
- Whether any of the four main benchmark documents regress at lower thresholds

**Key check:** At threshold 0.50, do healthy documents like the health policy or thesis get incorrectly flagged for repair?

**Deliverable:** Validated repair threshold with documented false-positive/false-negative tradeoff.

---

### Experiment 3B — Confidence Signal Ablation

**Why:** The `assess()` method in `src/sections/repair.py` combines five hardcoded penalty signals:

```python
Long document with too few sections:          -0.28
Duplicate title ratio >= 0.30:                -0.14
Noisy publisher title patterns >= 0.25:       -0.22
Research paper with weak canonical headings:  -0.18
Document too coarse for its length:           -0.24
```

None of these penalty magnitudes were derived from data. An ablation removing each signal one at a time on known-noisy documents would reveal which signals are load-bearing and which could be simplified or removed.

**Method:** For each signal, set its penalty to 0 and re-run `assess()` on:
- reportgeneration.pdf (known noisy — triggered repair correctly)
- reportgeneration2.pdf (known noisy — triggered repair correctly)
- HealthInsurance_Policy.pdf (known healthy — should not trigger repair)
- Final_Thesis_Leander_Antony_A.pdf (known healthy — should not trigger repair)

**What to measure:** Does removing each signal cause incorrect repair decisions on the known documents?

**Deliverable:** Simplified or validated signal set with evidence for which penalties matter.

---

## Priority 4 — Topology Edge Type Ablation

**Lower priority. The topology layer as a whole was validated by benchmark improvement. The specific edge types used in scope expansion are the remaining untested component.**

---

### Experiment 4A — Edge Type Ablation in Section Scope Expansion

**Why:** In `src/retrieval/hybrid.py _expand_section_scope()` the edge types used differ by constraint mode:

```python
soft_local:       previous_next, parent_child, semantic_neighbor
soft_multi_region: previous_next, same_region_family, semantic_neighbor
```

Never tested whether each edge type is contributing positively or whether some are adding noise.

**Method:** For each constraint mode, remove one edge type at a time and measure:
- Section hit rate
- Region hit rate
- Retrieval objective on thesis and policy benchmarks (different document structures)

**Specific questions to answer:**
- Is `semantic_neighbor` helping or introducing noise from weakly similar sections?
- Is `parent_child` meaningful given how sections are currently structured?
- Is `same_region_family` in soft_multi_region actually improving distributed question coverage?

**Deliverable:** Validated edge type set per constraint mode, or simplified expansion logic.

---

## Execution Order and Timeline

### Before applying for jobs (blocking)

1. **Experiment 1A** — Selector weight sweep under reorder-only (cheap, infrastructure exists)
2. **Experiment 1B** — Gap threshold sweep (moderate cost)
3. **Experiment 1C** — Spread condition isolation (moderate cost)
4. **Write ADR-010** — Complete selector story
5. **Experiment 2A** — Synopsis section window sweep (moderate cost)
6. **Experiment 2B** — Synopsis top-k grid (moderate cost)

### After applying (non-blocking but strengthens the project)

7. Experiment 2C — Global fallback top-k
8. Experiment 2D — Planner candidate region limit
9. Experiment 3A — Repair confidence threshold
10. Experiment 3B — Confidence signal ablation
11. Experiment 4A — Edge type ablation

---

## What to Say in Interviews About This

The defensible position is not "everything was swept." It is:

> "We have a clear hierarchy of what is benchmark-backed and what is engineering default. The core architectural decisions — reranker, chunking, planner thresholds, and the selector mode — all have evidence trails documented in the ADRs. The topology hyperparameters and selector trigger conditions are identified gaps with a concrete plan to address them. That is more honest and more rigorous than most production systems at this scale."

Specific talking points:

- **Selector story:** "We disabled the selector based on benchmark evidence showing faithfulness dropped. Later I traced through the code, identified that the ablation was conflating pruning with reordering, designed a controlled experiment to isolate the two effects, and the result overturned the original conclusion. Reorder-only mode gave us the highest faithfulness score in the project — 0.9657 versus 0.9310 for the baseline."

- **Topology defaults:** "The topology layer as a whole was validated by benchmark improvement across four document families. The specific hyperparameters like synopsis window size are set as engineering defaults. The honest next step is a synopsis window sweep similar to what we did for chunking — that work is planned."

- **Planner:** "The planner only activates on about 12% of queries. Even with perfectly calibrated thresholds the ceiling on improvement was always small, because routing was rarely the bottleneck — section quality and retrieval precision were. That is why subsequent work focused on topology planning and structure repair instead."

---

## Relevant Files for Each Experiment

| Experiment | Primary files to modify or reference |
|---|---|
| 1A — Selector weight sweep reorder-only | `src/evals/evidence_selector_weight_sweep.py`, `src/generation/evidence_selector.py` |
| 1B — Gap threshold sweep | `src/generation/evidence_selector.py (_should_select)`, `src/config.py` |
| 1C — Spread condition isolation | `src/generation/evidence_selector.py (_should_select)` |
| 2A — Synopsis section window | `src/config.py (synopsis_section_window)`, `src/retrieval/hybrid.py (_synopsis_first)` |
| 2B — Synopsis top-k grid | `src/config.py (synopsis_*_top_k)`, `src/evals/retrieval_eval.py` |
| 2C — Global fallback top-k | `src/config.py (global_fallback_top_k)`, `src/retrieval/hybrid.py (_global_summary_candidates)` |
| 2D — Planner region limit | `src/config.py (planner_candidate_region_limit)`, `src/retrieval/planner.py` |
| 3A — Repair threshold sweep | `src/config.py (structure_repair_confidence_threshold)`, `src/sections/repair.py` |
| 3B — Confidence signal ablation | `src/sections/repair.py (assess)` |
| 4A — Edge type ablation | `src/retrieval/hybrid.py (_expand_section_scope)` |

---

## Key Config Defaults Reference

```python
# Current defaults in src/config.py — values under review marked with *

chunk_size: int = 1200                              # validated
chunk_overlap: int = 240                            # validated
reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"  # validated
reranker_enabled: bool = True                       # validated
planner_confidence_threshold: float = 0.70          # validated
router_confidence_threshold: float = 0.62           # validated
evidence_selector_enabled: bool = False             # needs re-enabling in reorder-only mode
evidence_selector_rank_weight: float = 0.25         # validated under prune mode only *
evidence_selector_llm_weight: float = 0.75          # validated under prune mode only *
evidence_selector_gap_threshold: float = 0.08       # never swept *
evidence_selector_top_k: int = 4                    # never swept *
evidence_selector_max_evidence: int = 2             # irrelevant in reorder-only mode
synopsis_dense_top_k: int = 8                       # never swept *
synopsis_lexical_top_k: int = 8                     # never swept *
synopsis_fused_top_k: int = 5                       # never swept *
synopsis_section_window: int = 4                    # never swept *
planner_candidate_region_limit: int = 6             # never swept *
global_fallback_top_k: int = 4                      # never swept *
structure_repair_confidence_threshold: float = 0.62 # qualitative only *
```

---

*This document was produced as part of a systematic audit of the HelpmateAI architecture to identify which defaults are benchmark-backed and which require further validation. Updated April 2026.*
