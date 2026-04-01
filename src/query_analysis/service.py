from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class QueryProfile:
    query_type: str
    preferred_content_types: list[str] = field(default_factory=list)
    emphasized_terms: list[str] = field(default_factory=list)
    clause_terms: list[str] = field(default_factory=list)
    asks_for_definition: bool = False
    asks_for_process: bool = False


class QueryAnalyzer:
    @staticmethod
    def analyze(question: str) -> QueryProfile:
        lowered = question.lower()
        emphasized_terms = re.findall(r'"([^"]+)"', question)
        clause_terms = re.findall(r"\b\d+(?:\.\d+)+\b", question)
        summary_cues = (
            "main aim",
            "main focus",
            "main idea",
            "main conclusion",
            "primary topic",
            "primary subject",
            "key themes",
            "key issue",
            "future research directions",
            "future work",
            "next steps",
            "what did the thesis conclude",
            "what does the paper say about",
            "what challenge",
            "what major challenge",
            "why does the paper argue",
            "summarize",
            "overview",
        )

        if any(term in lowered for term in summary_cues):
            return QueryProfile(
                query_type="summary_lookup",
                preferred_content_types=["general", "benefit", "definition"],
                emphasized_terms=emphasized_terms,
                clause_terms=clause_terms,
                asks_for_definition=False,
                asks_for_process=False,
            )

        if any(term in lowered for term in ("what is", "what does", "how is", "define", "means")):
            if any(term in lowered for term in ("define", "means", "what is", "what does")):
                query_type = "definition_lookup"
                preferred_content_types = ["definition", "policy_admin", "provider_or_facility"]
                asks_for_definition = True
            else:
                query_type = "general_lookup"
                preferred_content_types = ["general", "benefit", "definition"]
                asks_for_definition = False
        else:
            query_type = "general_lookup"
            preferred_content_types = ["general", "benefit"]
            asks_for_definition = False

        asks_for_process = any(term in lowered for term in ("how can", "process", "steps", "claim", "avail", "happens if"))
        if asks_for_process:
            query_type = "process_lookup"
            preferred_content_types = ["claims_procedure", "policy_admin", "benefit"]

        if "waiting period" in lowered:
            query_type = "waiting_period_lookup"
            preferred_content_types = ["waiting_period", "definition", "benefit"]

        if any(term in lowered for term in ("exclusion", "not covered", "excluded")):
            query_type = "exclusion_lookup"
            preferred_content_types = ["exclusion", "waiting_period", "benefit"]

        if any(term in lowered for term in ("benefit", "cover", "coverage", "sum insured", "limit")):
            query_type = "benefit_lookup"
            preferred_content_types = ["benefit", "definition", "policy_admin"]

        return QueryProfile(
            query_type=query_type,
            preferred_content_types=preferred_content_types,
            emphasized_terms=emphasized_terms,
            clause_terms=clause_terms,
            asks_for_definition=asks_for_definition,
            asks_for_process=asks_for_process,
        )
