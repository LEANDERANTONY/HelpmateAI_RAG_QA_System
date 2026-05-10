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
        "First determine the required facts needed to fully answer the user's question. "
        "For multi-part, list, comparison, numeric, procedural, 'which', 'what', or 'how many' questions, "
        "set supported to true only when the evidence covers every required fact. "
        "If the evidence supports only part of the question, the answer itself must say which part is supported "
        "and which required fact is missing. In that case set supported to false and support_status to partial. "
        "If the evidence does not support any substantive answer to the question, say so clearly and do not guess. "
        "Whenever you state a fact that came from the evidence, end the sentence with the matching source marker exactly as written, like [Source 1] or [Source 2]. "
        "Use one marker per sentence at minimum; multiple markers are fine when a sentence draws from several sources. "
        "Do not invent source numbers — only use the indices that appear in the supplied evidence blocks. "
        "Return valid JSON only with keys: supported, support_status, answer, reason. "
        "support_status must be one of supported, partial, unsupported. "
        "Set supported to false when the supplied evidence cannot answer the question at all or only answers it partially. "
        "Use support_status=partial only when the evidence supports at least one substantive requested fact and the answer explicitly names the missing or unsupported required fact. "
        "Use support_status=unsupported when the evidence does not support a substantive answer. "
        "Do not use inferential wording such as 'implied', 'suggests', 'appears', 'likely', or 'could mean' in a supported=true answer; "
        "if the evidence requires that kind of inference, downgrade to supported=false and explain the gap.\n\n"
        f"{summary_instructions}"
        f"Question:\n{question}\n\n"
        f"Evidence:\n{joined_context}\n\n"
        "Return JSON only."
    )


def build_support_verification_prompt(question: str, answer: str, evidence: list[RetrievalCandidate]) -> str:
    context_blocks = []
    for index, candidate in enumerate(evidence, start=1):
        label = candidate.citation_label or candidate.metadata.get("page_label", "Document")
        context_blocks.append(f"[Source {index} | {label}]\n{candidate.text}")
    joined_context = "\n\n".join(context_blocks)
    return (
        "You are a strict evidence verifier for a document QA system. "
        "Check whether the proposed answer is fully and directly supported by the supplied evidence. "
        "First identify every required fact in the user's question. "
        "Then verify that the proposed answer provides those facts without contradiction, substitution, or unsupported inference. "
        "For numeric, comparison, list, and multi-part questions, every requested number/entity/relationship must be present and consistent. "
        "If the answer mixes a nearby value with a different requested value, or answers only part of the question, set supported to false. "
        "Do not use outside knowledge. Return valid JSON only with keys: supported, reason.\n\n"
        f"Question:\n{question}\n\n"
        f"Proposed answer:\n{answer}\n\n"
        f"Evidence:\n{joined_context}\n\n"
        "Return JSON only."
    )


def build_support_status_verification_prompt(
    *,
    question: str,
    answer: str,
    reason: str,
    claimed_support_status: str,
    evidence: list[RetrievalCandidate],
) -> str:
    context_blocks = []
    for index, candidate in enumerate(evidence, start=1):
        label = candidate.citation_label or candidate.metadata.get("page_label", "Document")
        context_blocks.append(f"[Source {index} | {label}]\n{candidate.text}")
    joined_context = "\n\n".join(context_blocks)
    return (
        "You are a strict support-status verifier for a document QA system. "
        "Classify the proposed answer against the supplied evidence. Do not rewrite the answer. "
        "Use support_status=supported only when every required fact in the user's question is directly covered by the evidence. "
        "Use support_status=partial only when the answer states at least one substantive fact that is grounded in evidence, "
        "at least one required fact is missing or ambiguous, and the visible answer itself acknowledges that gap. "
        "Use support_status=unsupported when the answer provides no substantive grounded answer, or when it hides a missing required fact. "
        "If the proposed answer says the evidence does not clearly state whether something is true, treat that as an acknowledged gap. "
        "Do not use outside knowledge. Return valid JSON only with keys: support_status, answer_acknowledges_gap, "
        "supported_facts, missing_or_ambiguous_facts, reason.\n\n"
        f"Question:\n{question}\n\n"
        f"Proposed answer:\n{answer}\n\n"
        f"Proposed answer reason:\n{reason}\n\n"
        f"Claimed support_status:\n{claimed_support_status}\n\n"
        f"Evidence:\n{joined_context}\n\n"
        "Return JSON only."
    )
