from __future__ import annotations

from dataclasses import replace

from src.config import get_settings
from src.evals.ragas_judge import get_ragas_judge_config


def test_ragas_judge_defaults_to_openai(monkeypatch):
    monkeypatch.delenv("HELPMATE_RAGAS_JUDGE_PROVIDER", raising=False)
    monkeypatch.delenv("HELPMATE_RAGAS_JUDGE_MODEL", raising=False)
    settings = get_settings()

    config = get_ragas_judge_config(settings)

    assert config.provider == "openai"
    assert config.model == settings.answer_model
    assert config.embedding_provider == "openai"


def test_ragas_judge_can_select_anthropic(monkeypatch):
    monkeypatch.setenv("HELPMATE_RAGAS_JUDGE_PROVIDER", "anthropic")
    monkeypatch.delenv("HELPMATE_RAGAS_JUDGE_MODEL", raising=False)
    settings = get_settings()

    config = get_ragas_judge_config(settings)

    assert config.provider == "anthropic"
    assert config.model.startswith("claude")


def test_ragas_judge_respects_model_override(monkeypatch):
    monkeypatch.setenv("HELPMATE_RAGAS_JUDGE_PROVIDER", "gemini")
    monkeypatch.setenv("HELPMATE_RAGAS_JUDGE_MODEL", "gemini-test")
    settings = replace(get_settings(), answer_model="ignored-model")

    config = get_ragas_judge_config(settings)

    assert config.provider == "gemini"
    assert config.model == "gemini-test"
