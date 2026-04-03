from __future__ import annotations

QUESTION_STARTERS = {
    "policy_document": [
        "What are the main exclusions or waiting periods in this policy?",
        "What obligations or deadlines should the insured person be aware of?",
        "How does the policy define key terms like grace period, cashless facility, or network provider?",
    ],
    "thesis_document": [
        "What is the main research aim of this thesis?",
        "What are the strongest findings or conclusions from this thesis?",
        "What future work or next steps does this thesis recommend?",
    ],
    "research_paper": [
        "What is the main focus of this paper?",
        "What are the most important findings or claims in this paper?",
        "What limitations, challenges, or future directions does this paper discuss?",
    ],
    "generic_longform": [
        "What are the key ideas or themes in this document?",
        "What important obligations, constraints, or conclusions does the document contain?",
        "What sections should I read first to understand the document quickly?",
    ],
}


def get_question_starters(document_style: str | None) -> list[str]:
    style = document_style or "generic_longform"
    return QUESTION_STARTERS.get(style, QUESTION_STARTERS["generic_longform"])
