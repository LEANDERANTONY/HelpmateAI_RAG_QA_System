from __future__ import annotations

import json
import re
from pathlib import Path
from statistics import mean

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from ragas import SingleTurnSample
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import Faithfulness, LLMContextPrecisionWithoutReference, ResponseRelevancy

from src.config import get_settings
from src.evals.openai_file_search_benchmark import OpenAIFileSearchBenchmark
from src.evals.vectara_benchmark import VectaraBenchmark
from src.generation import AnswerGenerator
from src.schemas import RetrievalCandidate, RetrievalResult


def _safe_mean(values: list[float]) -> float | None:
    if not values:
        return None
    return float(mean(values))


def _strip_references(answer_text: str) -> str:
    return re.split(r"\n\s*References:\s*\n", answer_text, maxsplit=1)[0].strip()


class VendorAnswerEvaluator:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.generator = AnswerGenerator(self.settings)
        self.available = bool(self.settings.openai_api_key)
        self._metrics = {}
        if self.available:
            llm = LangchainLLMWrapper(ChatOpenAI(model=self.settings.answer_model, api_key=self.settings.openai_api_key))
            embeddings = LangchainEmbeddingsWrapper(
                OpenAIEmbeddings(model=self.settings.embedding_model, api_key=self.settings.openai_api_key)
            )
            self._metrics = {
                "faithfulness": Faithfulness(llm=llm),
                "answer_relevancy": ResponseRelevancy(llm=llm, embeddings=embeddings),
                "context_precision": LLMContextPrecisionWithoutReference(llm=llm),
            }

    @staticmethod
    def _candidates(vendor_name: str, snippets: list[dict]) -> list[RetrievalCandidate]:
        candidates: list[RetrievalCandidate] = []
        for index, snippet in enumerate(snippets):
            metadata = dict(snippet.get("metadata", {}))
            page_label = metadata.get("page_label", f"{vendor_name.title()} Result {index + 1}")
            candidates.append(
                RetrievalCandidate(
                    chunk_id=f"{vendor_name}-{index + 1}",
                    text=snippet.get("text", ""),
                    metadata=metadata,
                    fused_score=float(max(0.0, 1.0 - (index * 0.1))),
                    citation_label=page_label,
                )
            )
        return candidates

    def _generate_answer(self, question: str, snippets: list[dict], vendor_name: str):
        retrieval = RetrievalResult(
            question=question,
            candidates=self._candidates(vendor_name, snippets),
            route_used=f"{vendor_name}_retrieval",
            query_used=question,
            strategy_notes=[f"Answer generated from {vendor_name} retrieval context."],
        )
        answer = self.generator.generate(question, retrieval)
        return answer

    def _ragas_scores(self, question: str, answer_text: str, contexts: list[str]) -> dict:
        if not self.available:
            return {}

        sample = SingleTurnSample(
            user_input=question,
            response=answer_text,
            retrieved_contexts=contexts,
        )
        scores: dict[str, float] = {}
        for name, metric in self._metrics.items():
            try:
                scores[name] = float(metric.single_turn_score(sample))
            except Exception as exc:
                scores[f"{name}_error"] = str(exc)
        return scores

    def evaluate(self, dataset_path: str | Path, document_path: str | Path) -> dict:
        dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
        openai_search = OpenAIFileSearchBenchmark()
        vectara_search = VectaraBenchmark()

        payload: dict[str, dict] = {}
        for vendor_name, provider in (("openai", openai_search), ("vectara", vectara_search)):
            results: list[dict] = []
            ragas_faithfulness: list[float] = []
            ragas_answer_relevancy: list[float] = []
            ragas_context_precision: list[float] = []

            for item in dataset:
                question = item["question"]
                search = provider.search(document_path, question)
                snippets = search["results"]
                answer = self._generate_answer(question, snippets, vendor_name)
                cleaned_answer = _strip_references(answer.answer)
                contexts = [snippet["text"] for snippet in snippets if snippet.get("text")]
                ragas_scores = self._ragas_scores(question, cleaned_answer, contexts)

                if "faithfulness" in ragas_scores:
                    ragas_faithfulness.append(ragas_scores["faithfulness"])
                if "answer_relevancy" in ragas_scores:
                    ragas_answer_relevancy.append(ragas_scores["answer_relevancy"])
                if "context_precision" in ragas_scores:
                    ragas_context_precision.append(ragas_scores["context_precision"])

                row = {
                    "question": question,
                    "supported": answer.supported,
                    "answer_preview": cleaned_answer[:300],
                    "citations": answer.citations,
                    "ragas": ragas_scores,
                }
                results.append(row)

            payload[vendor_name] = {
                "dataset_size": len(dataset),
                "ragas_faithfulness_mean": _safe_mean(ragas_faithfulness),
                "ragas_answer_relevancy_mean": _safe_mean(ragas_answer_relevancy),
                "ragas_context_precision_mean": _safe_mean(ragas_context_precision),
                "results": results,
            }

        return payload
