from __future__ import annotations

from src.schemas import RetrievalCandidate


def build_grounded_prompt(question: str, evidence: list[RetrievalCandidate]) -> str:
    context_blocks = []
    for index, candidate in enumerate(evidence, start=1):
        label = candidate.citation_label or candidate.metadata.get("page_label", "Document")
        context_blocks.append(f"[Source {index} | {label}]\n{candidate.text}")
    joined_context = "\n\n".join(context_blocks)
    return (
        "You are a grounded document QA assistant. "
        "Answer only from the provided evidence. "
        "If the evidence is insufficient, say so clearly and do not guess. "
        "When you reference evidence, cite it using the source labels such as [Source 1]. "
        "Return valid JSON only with keys: supported, answer, reason. "
        "If the answer is not supported, set supported to false, keep answer very short, and explain the gap in reason.\n\n"
        f"Question:\n{question}\n\n"
        f"Evidence:\n{joined_context}\n\n"
        "Return JSON only."
    )
