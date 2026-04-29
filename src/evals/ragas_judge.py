from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from typing import Any

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import Faithfulness, LLMContextPrecisionWithoutReference, ResponseRelevancy

from src.config import Settings


@dataclass(frozen=True)
class RagasJudgeConfig:
    provider: str
    model: str
    embedding_provider: str
    embedding_model: str

    def to_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "model": self.model,
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
        }


def get_ragas_judge_config(settings: Settings) -> RagasJudgeConfig:
    provider = os.getenv("HELPMATE_RAGAS_JUDGE_PROVIDER", "openai").strip().lower()
    if provider == "anthropic":
        model = os.getenv("HELPMATE_RAGAS_JUDGE_MODEL", "claude-3-5-sonnet-latest")
    elif provider == "gemini":
        model = os.getenv("HELPMATE_RAGAS_JUDGE_MODEL", "gemini-2.5-flash")
    else:
        provider = "openai"
        model = os.getenv("HELPMATE_RAGAS_JUDGE_MODEL", settings.answer_model)
    return RagasJudgeConfig(
        provider=provider,
        model=model,
        embedding_provider=os.getenv("HELPMATE_RAGAS_EMBEDDING_PROVIDER", "openai").strip().lower(),
        embedding_model=os.getenv("HELPMATE_RAGAS_EMBEDDING_MODEL", settings.embedding_model),
    )


def build_ragas_metrics(settings: Settings, *, config: RagasJudgeConfig | None = None) -> tuple[dict[str, Any], dict[str, str]]:
    config = config or get_ragas_judge_config(settings)
    if not _provider_available(config.provider):
        return {}, {
            "reason": f"{config.provider} judge is not configured.",
            **config.to_dict(),
        }
    if config.embedding_provider != "openai" or not settings.openai_api_key:
        return {}, {
            "reason": "RAGAS ResponseRelevancy currently requires OpenAI embeddings in this project harness.",
            **config.to_dict(),
        }

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=DeprecationWarning)
        llm = LangchainLLMWrapper(_build_judge_llm(settings, config))
        embeddings = LangchainEmbeddingsWrapper(
            OpenAIEmbeddings(model=config.embedding_model, api_key=settings.openai_api_key)
        )
    return {
        "faithfulness": Faithfulness(llm=llm),
        "answer_relevancy": ResponseRelevancy(llm=llm, embeddings=embeddings),
        "context_precision": LLMContextPrecisionWithoutReference(llm=llm),
    }, config.to_dict()


def _provider_available(provider: str) -> bool:
    if provider == "openai":
        return bool(os.getenv("OPENAI_API_KEY"))
    if provider == "anthropic":
        return bool(os.getenv("ANTHROPIC_API_KEY"))
    if provider == "gemini":
        return bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))
    return False


def _build_judge_llm(settings: Settings, config: RagasJudgeConfig):
    if config.provider == "openai":
        return ChatOpenAI(model=config.model, api_key=settings.openai_api_key)
    if config.provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as exc:
            raise RuntimeError("Install langchain-anthropic to use HELPMATE_RAGAS_JUDGE_PROVIDER=anthropic.") from exc
        return ChatAnthropic(model=config.model, api_key=os.getenv("ANTHROPIC_API_KEY"))
    if config.provider == "gemini":
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        return ChatOpenAI(
            model=config.model,
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
    raise ValueError(f"Unsupported RAGAS judge provider: {config.provider}")
