from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


ROOT_DIR = Path(__file__).resolve().parent.parent


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str = "HelpmateAI"
    app_tagline: str = "Grounded answers for long-form documents."
    docs_dir: Path = ROOT_DIR / "docs"
    data_dir: Path = ROOT_DIR / "data"
    uploads_dir: Path = ROOT_DIR / "data" / "uploads"
    indexes_dir: Path = ROOT_DIR / "data" / "indexes"
    cache_dir: Path = ROOT_DIR / "data" / "cache"
    chunk_size: int = _env_int("HELPMATE_CHUNK_SIZE", 1200)
    chunk_overlap: int = _env_int("HELPMATE_CHUNK_OVERLAP", 180)
    dense_top_k: int = _env_int("HELPMATE_DENSE_TOP_K", 10)
    lexical_top_k: int = _env_int("HELPMATE_LEXICAL_TOP_K", 10)
    fused_top_k: int = _env_int("HELPMATE_FUSED_TOP_K", 12)
    final_top_k: int = _env_int("HELPMATE_FINAL_TOP_K", 4)
    query_cache_limit: int = _env_int("HELPMATE_QUERY_CACHE_LIMIT", 250)
    adaptive_dense_top_k: int = _env_int("HELPMATE_ADAPTIVE_DENSE_TOP_K", 18)
    adaptive_lexical_top_k: int = _env_int("HELPMATE_ADAPTIVE_LEXICAL_TOP_K", 18)
    adaptive_fused_top_k: int = _env_int("HELPMATE_ADAPTIVE_FUSED_TOP_K", 18)
    reranker_enabled: bool = _env_bool("HELPMATE_RERANKER_ENABLED", True)
    reranker_model: str = os.getenv("HELPMATE_RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
    query_rewrite_enabled: bool = _env_bool("HELPMATE_QUERY_REWRITE_ENABLED", True)
    query_rewrite_model: str = os.getenv("HELPMATE_QUERY_REWRITE_MODEL", "gpt-4o-mini")
    embedding_model: str = os.getenv("HELPMATE_EMBEDDING_MODEL", "text-embedding-3-small")
    answer_model: str = os.getenv("HELPMATE_ANSWER_MODEL", "gpt-4o-mini")
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    retrieval_version: str = os.getenv("HELPMATE_RETRIEVAL_VERSION", "v3")
    generation_version: str = os.getenv("HELPMATE_GENERATION_VERSION", "v3")
    cache_similarity_threshold: float = _env_float("HELPMATE_CACHE_SIMILARITY_THRESHOLD", 0.94)
    weak_evidence_score_threshold: float = _env_float("HELPMATE_WEAK_EVIDENCE_SCORE_THRESHOLD", 0.03)
    lexical_hit_threshold: float = _env_float("HELPMATE_LEXICAL_HIT_THRESHOLD", 0.02)

    def ensure_dirs(self) -> None:
        evals_dir = self.docs_dir / "evals"
        for path in (self.data_dir, self.uploads_dir, self.indexes_dir, self.cache_dir, self.docs_dir, evals_dir):
            path.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
