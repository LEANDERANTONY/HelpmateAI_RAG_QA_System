from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class QueryProfile:
    query_type: str
    intent_type: str
    evidence_spread: str
    preferred_content_types: list[str] = field(default_factory=list)
    emphasized_terms: list[str] = field(default_factory=list)
    clause_terms: list[str] = field(default_factory=list)
    section_terms: list[str] = field(default_factory=list)
    asks_for_definition: bool = False
    asks_for_process: bool = False
    asks_for_numeric: bool = False
    asks_for_comparison: bool = False
    asks_for_specific_detail: bool = False
    has_explicit_constraint: bool = False


class QueryAnalyzer:
    SUMMARY_CUES = (
        "big picture",
        "future directions",
        "future work",
        "key takeaway",
        "main aim",
        "main conclusion",
        "main focus",
        "main idea",
        "next steps",
        "overall",
        "overview",
        "primary topic",
        "research objectives",
        "summarize",
        "summary",
        "takeaway",
        "what did the thesis conclude",
        "what does the paper say about",
    )
    COMPARISON_CUES = ("compare", "comparison", "different from", "differ", "versus", "vs ")
    PROCESS_CUES = (
        "architecture",
        "how can",
        "how do",
        "how was",
        "implemented",
        "implementation",
        "method",
        "methodology",
        "procedure",
        "process",
        "steps",
        "workflow",
    )
    DETAIL_CUES = (
        "architecture",
        "implemented",
        "implementation",
        "model used",
        "used in the model",
        "used for the model",
    )
    NUMERIC_CUES = (
        "amount",
        "auc",
        "count",
        "figure",
        "figures",
        "how many",
        "how much",
        "metric",
        "metrics",
        "number",
        "numbers",
        "percent",
        "percentage",
        "ratio",
        "rate",
        "rates",
        "score",
        "scores",
        "total",
        "value",
        "values",
    )
    DISTRIBUTED_CUES = (
        "across",
        "all of the",
        "all the",
        "different",
        "everywhere",
        "kinds of",
        "mentions",
        "throughout",
        "various",
        "what are the",
        "which are the",
    )
    EXPLICIT_SECTION_CUES = ("section ", "chapter ", "within section", "within chapter")
    GLOBAL_SUMMARY_CUES = (
        "future directions",
        "future work",
        "key takeaway",
        "main aim",
        "main conclusion",
        "main focus",
        "main idea",
        "next steps",
        "overall",
        "overview",
        "primary topic",
        "purpose",
        "research objective",
        "research objectives",
        "scope",
    )

    @staticmethod
    def _contains_any(lowered: str, cues: tuple[str, ...]) -> bool:
        return any(cue in lowered for cue in cues)

    @classmethod
    def _preferred_content_types(
        cls,
        *,
        intent_type: str,
        asks_for_definition: bool,
        asks_for_process: bool,
        asks_for_numeric: bool,
    ) -> list[str]:
        if asks_for_definition:
            return ["definition", "general"]
        if asks_for_process:
            return ["methodology", "general", "results"]
        if asks_for_numeric:
            return ["results", "general"]
        if intent_type == "summary":
            return ["general", "results"]
        if intent_type in {"comparison", "cross_cutting"}:
            return ["general", "definition", "results"]
        return ["general", "definition"]

    @classmethod
    def analyze(cls, question: str) -> QueryProfile:
        lowered = question.lower()
        emphasized_terms = re.findall(r'"([^"]+)"', question)
        clause_terms = re.findall(r"(?:clause|section)\s+(\d+(?:\.\d+)+)", question, flags=re.IGNORECASE)
        section_terms = emphasized_terms + re.findall(r"(?:section|chapter)\s+([A-Za-z][A-Za-z0-9 ._-]*)", question, flags=re.IGNORECASE)
        explicit_page = bool(re.search(r"\bpage(?:s)?\s+\d+\b", lowered))
        explicit_clause = bool(clause_terms)
        explicit_section = bool(section_terms) or cls._contains_any(lowered, cls.EXPLICIT_SECTION_CUES)
        has_explicit_constraint = explicit_page or explicit_clause or bool(section_terms)

        asks_for_summary = cls._contains_any(lowered, cls.SUMMARY_CUES)
        asks_for_specific_detail = (
            cls._contains_any(lowered, cls.DETAIL_CUES)
            or bool(re.search(r"\bwhat\b.*\b(features?|variables?|inputs?|signals?|factors?)\b.*\bused\b", lowered))
        )
        asks_for_definition = not asks_for_summary and cls._contains_any(
            lowered,
            ("define", "definition of", "means", "what is", "what does"),
        )
        asks_for_process = not asks_for_summary and (cls._contains_any(lowered, cls.PROCESS_CUES) or asks_for_specific_detail)
        asks_for_numeric = cls._contains_any(lowered, cls.NUMERIC_CUES)
        asks_for_comparison = cls._contains_any(lowered, cls.COMPARISON_CUES)
        looks_distributed = cls._contains_any(lowered, cls.DISTRIBUTED_CUES) or bool(
            re.search(r"\bwhat\b.*\b[a-z0-9_-]{3,}s\b.*\bapply\b", lowered)
        )
        asks_for_global_summary = asks_for_summary and cls._contains_any(lowered, cls.GLOBAL_SUMMARY_CUES)

        if asks_for_comparison:
            intent_type = "comparison"
        elif asks_for_summary:
            intent_type = "summary"
        elif asks_for_process:
            intent_type = "procedure"
        elif asks_for_numeric:
            intent_type = "numeric"
        elif looks_distributed:
            intent_type = "cross_cutting"
        else:
            intent_type = "lookup"

        if explicit_page or explicit_clause:
            evidence_spread = "atomic"
        elif intent_type == "summary" and (
            asks_for_global_summary
            or cls._contains_any(lowered, ("what did the thesis conclude",))
        ):
            evidence_spread = "global"
        elif intent_type in {"comparison", "cross_cutting"} or looks_distributed:
            evidence_spread = "distributed"
        elif intent_type in {"summary", "numeric"} or explicit_section:
            evidence_spread = "sectional"
        elif intent_type == "procedure":
            evidence_spread = "sectional" if asks_for_specific_detail else "sectional"
        else:
            evidence_spread = "atomic"

        if intent_type == "summary":
            query_type = "summary_lookup"
        elif asks_for_definition:
            query_type = "definition_lookup"
        elif intent_type == "procedure":
            query_type = "process_lookup"
        elif intent_type == "numeric":
            query_type = "numeric_lookup"
        elif intent_type == "comparison":
            query_type = "comparison_lookup"
        elif intent_type == "cross_cutting":
            query_type = "cross_cutting_lookup"
        else:
            query_type = "general_lookup"

        return QueryProfile(
            query_type=query_type,
            intent_type=intent_type,
            evidence_spread=evidence_spread,
            preferred_content_types=cls._preferred_content_types(
                intent_type=intent_type,
                asks_for_definition=asks_for_definition,
                asks_for_process=asks_for_process,
                asks_for_numeric=asks_for_numeric,
            ),
            emphasized_terms=emphasized_terms,
            clause_terms=clause_terms,
            section_terms=section_terms,
            asks_for_definition=asks_for_definition,
            asks_for_process=asks_for_process,
            asks_for_numeric=asks_for_numeric,
            asks_for_comparison=asks_for_comparison,
            asks_for_specific_detail=asks_for_specific_detail,
            has_explicit_constraint=has_explicit_constraint,
        )
