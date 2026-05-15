from __future__ import annotations

from src.schemas import RetrievalCandidate


# Prompts are loaded from ``backend/prompts/<task>/v1.json`` via
# ``backend.prompt_registry.get_prompt(...)`` so the static template
# text lives on disk (reviewable as JSON diffs, version-pinned in
# registry.json) rather than inlined as Python string literals. The
# builders below still own the data-shaping logic (evidence-block
# concatenation, summary-mode focus detection) — only the template
# itself moved.
#
# We import lazily inside each builder to avoid a hard
# ``src/`` → ``backend/`` import at module-load time. The src/ layer
# is the pure RAG core; importing backend/ at top-level would couple
# the test fixtures in tests/ that exercise src/ in isolation to
# the backend.prompt_registry side effects. Lazy import keeps the
# dependency edge at runtime only.


def _summary_focus(question: str) -> str:
    lowered = question.lower()
    if any(term in lowered for term in ("finding", "findings", "headline", "result", "results", "outcome", "performance")):
        return "findings"
    if any(term in lowered for term in ("future", "conclusion", "conclusions", "limitations", "implications", "recommendations")):
        return "late"
    if any(term in lowered for term in ("about", "overview", "focus", "purpose", "scope", "contribution", "contributions", "aim", "objective")):
        return "overview"
    return "balanced"


def _join_evidence(evidence: list[RetrievalCandidate]) -> str:
    """Build the ``[Source N | label]\\n<text>`` blocks the LLM sees.

    Pulled out of the individual builders so all four LLM-prompt
    builders below share the same evidence shape — keeps the
    citation marker contract in one place and avoids drift between
    the answer-generation prompt and the verifier prompts.
    """
    blocks: list[str] = []
    for index, candidate in enumerate(evidence, start=1):
        label = candidate.citation_label or candidate.metadata.get("page_label", "Document")
        blocks.append(f"[Source {index} | {label}]\n{candidate.text}")
    return "\n\n".join(blocks)


def _summary_instructions_for(question: str, *, summary_mode: bool) -> str:
    """Pick the focus-specific summary preamble for global summary
    questions. Returns "" for non-summary mode so callers can drop the
    string into the prompt's ``{{summary_instructions}}`` slot without
    a conditional."""
    if not summary_mode:
        return ""
    focus = _summary_focus(question)
    if focus == "findings":
        return (
            "This is a broad summary question about findings. "
            "Synthesize the major findings across the evidence, lead with the central takeaway, "
            "and mention only findings that are clearly supported.\n"
        )
    if focus == "late":
        return (
            "This is a broad summary question about conclusions or next steps. "
            "Synthesize the supported conclusion-level points across the evidence and avoid drifting into unrelated methods.\n"
        )
    return (
        "This is a broad high-level summary question. "
        "Combine the overview-style evidence into one concise explanation of what the document is about, "
        "optionally followed by the most important supported finding.\n"
    )


def build_grounded_prompt(question: str, evidence: list[RetrievalCandidate], *, summary_mode: bool = False) -> str:
    """Render the answer-generation prompt from
    ``backend/prompts/answer_generation/v1.json`` via the registry.

    Returns just the ``user`` content — the matching system message
    (``"You answer questions using only supplied document evidence."``)
    is passed alongside at the call site in ``AnswerGenerator``. Both
    halves stay aligned via the test in tests/test_prompt_registry.py
    that pins the registry version + the call-site string together.
    """
    from backend.prompt_registry import get_prompt

    template = get_prompt("answer_generation", "v1")
    rendered = template.render(
        question=question,
        evidence=_join_evidence(evidence),
        summary_instructions=_summary_instructions_for(question, summary_mode=summary_mode),
    )
    return rendered["user"]


def build_support_verification_prompt(question: str, answer: str, evidence: list[RetrievalCandidate]) -> str:
    """Render the second-opinion verifier prompt from
    ``backend/prompts/support_verifier/v1.json`` via the registry.
    Used by ``AnswerGenerator.verify_supported_answer``.
    """
    from backend.prompt_registry import get_prompt

    template = get_prompt("support_verifier", "v1")
    rendered = template.render(
        question=question,
        answer=answer,
        evidence=_join_evidence(evidence),
    )
    return rendered["user"]


def build_support_status_verification_prompt(
    *,
    question: str,
    answer: str,
    reason: str,
    claimed_support_status: str,
    evidence: list[RetrievalCandidate],
) -> str:
    """Render the three-class support-status verifier prompt from
    ``backend/prompts/support_status_verifier/v1.json`` via the registry.
    Used by ``AnswerGenerator.verify_support_status``.
    """
    from backend.prompt_registry import get_prompt

    template = get_prompt("support_status_verifier", "v1")
    rendered = template.render(
        question=question,
        answer=answer,
        reason=reason,
        claimed_support_status=claimed_support_status,
        evidence=_join_evidence(evidence),
    )
    return rendered["user"]
