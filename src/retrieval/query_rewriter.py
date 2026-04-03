from __future__ import annotations

import re

from src.config import Settings
from src.query_analysis import QueryProfile


class QueryRewriter:
    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def _normalize(question: str) -> str:
        return " ".join(question.strip().split())

    @staticmethod
    def _dedupe(variants: list[str]) -> list[str]:
        return list(dict.fromkeys(variant for variant in variants if variant))[:3]

    @staticmethod
    def _factual_rewrites(question: str) -> list[str]:
        compact = QueryRewriter._normalize(question)
        lowered = compact.lower()
        variants: list[str] = [compact]

        if any(term in lowered for term in ("free look", "cooling-off")):
            variants.append(f"{compact} right to examine policy cancellation refund days")
        if "waiting period" in lowered:
            variants.append(f"{compact} elimination period effective date days")
        if any(term in lowered for term in ("pre-hospitalization", "post-hospitalization", "pre hospitalization", "post hospitalization")):
            variants.append(
                f"{compact} pre hospitalization post hospitalization 60 days 90 days reimbursement facility hospitalization expenses"
            )
        if "co-payment" in lowered or "copayment" in lowered:
            variants.append(f"{compact} claim assessment admissible claim amount payable deductible")
        if any(term in lowered for term in ("cashless", "network provider")):
            variants.append(f"{compact} pre authorization health card network provider written approval")
        page_match = re.search(r"\bpage\s+(\d+)\b", lowered)
        if page_match:
            variants.append(f"{compact} {page_match.group(0)} exact clause")
        return QueryRewriter._dedupe(variants)

    @staticmethod
    def _summary_rewrites(question: str) -> list[str]:
        compact = QueryRewriter._normalize(question)
        lowered = compact.lower()
        variants: list[str] = [compact]

        if any(term in lowered for term in ("future work", "future directions", "next steps", "follow-up work", "future research")):
            variants.extend(
                [
                    f"{compact} future work recommendations discussion conclusion",
                    f"{compact} future directions recommendations limitations follow-up research",
                ]
            )

        if any(term in lowered for term in ("main focus", "main aim", "main idea", "main argument", "primary topic", "main conclusion")):
            variants.extend(
                [
                    f"{compact} abstract introduction overview objective contribution",
                    f"{compact} paper overview aims objectives abstract conclusion",
                ]
            )

        if any(term in lowered for term in ("challenge", "limitation", "limitations", "barrier", "clinical adoption", "bottleneck")):
            variants.extend(
                [
                    f"{compact} challenges limitations discussion conclusion",
                    f"{compact} barrier bottleneck discussion adoption limitations",
                ]
            )

        if any(term in lowered for term in ("what does the paper say about", "what did the thesis conclude", "why does the paper argue")):
            variants.append(f"{compact} discussion conclusion results overview")

        if "paper" in lowered:
            variants.append(f"{compact} abstract introduction discussion conclusion")
        if "thesis" in lowered:
            variants.append(f"{compact} abstract chapter conclusion recommendations future work")

        return QueryRewriter._dedupe(variants)

    def rewrite(self, question: str, query_profile: QueryProfile | None = None) -> list[str]:
        if not self.settings.query_rewrite_enabled:
            return [self._normalize(question)]

        if query_profile and query_profile.query_type == "summary_lookup":
            return self._summary_rewrites(question)
        return self._factual_rewrites(question)
