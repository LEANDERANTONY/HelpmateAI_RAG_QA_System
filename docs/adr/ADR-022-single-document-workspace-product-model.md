# ADR-022: Single-Document Workspace Is The Product Model

Date: 2026-05-17

Status: Shipped (records existing behaviour + the positioning constraint it implies)

## Context

A landing-vs-code audit found the pricing cards promising a multi-document capability the product does not have:

- Free: "3 active documents"; Pro: "Unlimited documents"; the Free/Pro blurbs ("a few documents", "Unlimited docs for individuals"); and an in-code COGS comment asserting *"'Unlimited documents' is safe at all paid tiers"*.

The actual model, verified in code: `backend/main.py::_find_active_workspace_document` lists the user's documents, keeps the single most-recently-active one as `primary`, and **deletes every other one** (`for stale in active_documents[1:]: _delete_workspace_records(stale)`) on every workspace resolve. The code comment states it outright: *"one workspace per user … If multi-doc lands"*. There is no `GET /documents` list endpoint and no document switcher in the frontend. A new upload supersedes and deletes the prior document. `doc_cap` in `TIER_LIMITS` (3 / soft-ceiling) gates a count the workspace resolver structurally keeps at ~1 — it is a **vestigial gate for a capability that does not exist yet**, not a real allowance.

So the multi-doc claims misrepresented the core product model on every tier, including the Free acquisition tier — and Pro is self-serve Lemon Squeezy checkout, making it a consumer-protection liability the moment the variant goes live.

This ADR is not proposing to *change* the model — single-document is a deliberate, working scope. It records that decision and the constraint it places on positioning, so the misleading copy doesn't get re-added and a future contributor doesn't assume a document library exists.

## Decision

**HelpmateAI is a single-document QA workspace.** One active document per user; uploading a new one supersedes (and the resolver deletes) the prior. This is the intended scope for v1 — storage is trivially cheap and not a pricing lever; tiers differentiate on the real cost drivers: file-size cap, questions/month, Premium (GPT-5.5) answer cap, and history retention.

Consequences for surfaces:

- **Pricing copy must not claim multi-document.** Removed Free "3 active documents" and Pro "Unlimited documents"; reblurbed Free/Pro to real wired differentiators. Every remaining bullet on every tier is verified against `backend/tiers.py` + the live gates.
- **The COGS comment in `pricing.tsx` carries an explicit guard note**: document count is deliberately NOT a tier lever; don't re-add "N documents" / "Unlimited documents" copy — it misrepresents the model. (The valid LLM/question-cap margin math is retained.)
- **`doc_cap` stays in `TIER_LIMITS`** (harmless, and it becomes meaningful the day multi-doc is built — the "If multi-doc lands" comment anticipates this). It is documented here as currently vestigial so nobody mistakes its presence for a shipped multi-doc allowance.

## Consequences

- **Positive.** The landing now promises only what the code delivers; the in-code guard makes the constraint discoverable at the point of edit, not just in this ADR.
- **Neutral.** If multi-document is ever built, this ADR is superseded by the ADR that introduces it (and `doc_cap` activates as designed). Until then, "single-document workspace" is the honest one-line description of the product.
- **Negative.** "Unlimited documents" was a headline Pro selling point; dropping it narrows the surface-level pitch. Mitigation: the retained differentiators (bigger files, 500 q/mo, Premium GPT-5.5 answers, 1-year retention) are real, wired, and tier-distinct.

## Alternatives considered

- **Build real multi-document now.** Out of scope for v1 and a large effort (library UI, switcher, per-doc retention, list endpoint); the honest fix is copy, not a feature scramble.
- **Keep the copy, accept the gap.** Rejected — false capability on a self-serve paid tier is a liability, not a deferrable polish item.
