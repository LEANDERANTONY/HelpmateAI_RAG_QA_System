from __future__ import annotations

from src.schemas import RetrievalCandidate


def _summary_focus(question: str) -> str:
    lowered = question.lower()
    if any(term in lowered for term in ("finding", "findings", "headline", "result", "results", "outcome", "performance")):
        return "findings"
    if any(term in lowered for term in ("future", "conclusion", "conclusions", "limitations", "implications", "recommendations")):
        return "late"
    if any(term in lowered for term in ("about", "overview", "focus", "purpose", "scope", "contribution", "contributions", "aim", "objective")):
        return "overview"
    return "balanced"


def build_grounded_prompt(question: str, evidence: list[RetrievalCandidate], *, summary_mode: bool = False) -> str:
    context_blocks = []
    for index, candidate in enumerate(evidence, start=1):
        label = candidate.citation_label or candidate.metadata.get("page_label", "Document")
        context_blocks.append(f"[Source {index} | {label}]\n{candidate.text}")
    joined_context = "\n\n".join(context_blocks)
    summary_instructions = ""
    if summary_mode:
        focus = _summary_focus(question)
        if focus == "findings":
            summary_instructions = (
                "This is a broad summary question about findings. "
                "Synthesize the major findings across the evidence, lead with the central takeaway, "
                "and mention only findings that are clearly supported.\n"
            )
        elif focus == "late":
            summary_instructions = (
                "This is a broad summary question about conclusions or next steps. "
                "Synthesize the supported conclusion-level points across the evidence and avoid drifting into unrelated methods.\n"
            )
        else:
            summary_instructions = (
                "This is a broad high-level summary question. "
                "Combine the overview-style evidence into one concise explanation of what the document is about, "
                "optionally followed by the most important supported finding.\n"
            )
    return (
        "You are a grounded document QA assistant. "
        "Answer only from the provided evidence. "
        "If the evidence only supports part of the question, answer the supported part directly, "
        "set supported to true, and explain the missing coverage in reason. "
        "If the evidence does not support any substantive answer to the question, say so clearly and do not guess. "
        "When you reference evidence, cite it using the source labels such as [Source 1]. "
        "Return valid JSON only with keys: supported, answer, reason. "
        "Set supported to false only when the supplied evidence cannot answer the question at all; "
        "in that case, keep answer very short and explain the gap in reason.\n\n"
        f"{summary_instructions}"
        f"Question:\n{question}\n\n"
        f"Evidence:\n{joined_context}\n\n"
        "Return JSON only."
    )
