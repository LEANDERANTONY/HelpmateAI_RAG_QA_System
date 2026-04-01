from __future__ import annotations

import re

from src.config import Settings


class QueryRewriter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = None
        if settings.openai_api_key and settings.query_rewrite_enabled:
            try:
                from openai import OpenAI

                self.client = OpenAI(api_key=settings.openai_api_key)
            except Exception:
                self.client = None

    @staticmethod
    def _heuristic_rewrites(question: str) -> list[str]:
        compact = " ".join(question.strip().split())
        variants: list[str] = [compact]
        lowered = compact.lower()
        variants.append(f"{compact} policy clause definition exclusion waiting period benefit deadline")
        if "free look" in lowered:
            variants.append(f"{compact} right to examine policy cancellation refund days")
        if "waiting period" in lowered:
            variants.append(f"{compact} elimination period effective date days")
        if "pre-hospitalization" in lowered or "post-hospitalization" in lowered or "pre hospitalization" in lowered or "post hospitalization" in lowered:
            variants.append(
                f"{compact} pre hospitalization post hospitalization 60 days 90 days reimbursement facility hospitalization expenses"
            )
        if "co-payment" in lowered or "copayment" in lowered:
            variants.append(f"{compact} claim assessment admissible claim amount payable deductible")
        page_match = re.search(r"\bpage\s+(\d+)\b", lowered)
        if page_match:
            variants.append(f"{compact} {page_match.group(0)} exact clause")
        return list(dict.fromkeys(variant for variant in variants if variant))

    def rewrite(self, question: str) -> list[str]:
        if self.client is None:
            return self._heuristic_rewrites(question)

        prompt = (
            "Rewrite the question into up to three retrieval-friendly variants for a long-document QA system. "
            "Preserve the user's intent, include domain synonyms when useful, and return one variant per line without numbering.\n\n"
            f"Question: {question}"
        )
        try:
            response = self.client.chat.completions.create(
                model=self.settings.query_rewrite_model,
                messages=[
                    {"role": "system", "content": "You rewrite document QA queries for retrieval quality."},
                    {"role": "user", "content": prompt},
                ],
            )
            content = response.choices[0].message.content or ""
            variants = [line.strip("- ").strip() for line in content.splitlines() if line.strip()]
            if question not in variants:
                variants.insert(0, question)
            return list(dict.fromkeys(variant for variant in variants if variant))[:3]
        except Exception:
            return self._heuristic_rewrites(question)
