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
    index_schema_version: str = os.getenv("HELPMATE_INDEX_SCHEMA_VERSION", "v10")
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
    section_dense_top_k: int = _env_int("HELPMATE_SECTION_DENSE_TOP_K", 6)
    section_lexical_top_k: int = _env_int("HELPMATE_SECTION_LEXICAL_TOP_K", 6)
    section_fused_top_k: int = _env_int("HELPMATE_SECTION_FUSED_TOP_K", 4)
    section_chunk_window: int = _env_int("HELPMATE_SECTION_CHUNK_WINDOW", 2)
    synopsis_dense_top_k: int = _env_int("HELPMATE_SYNOPSIS_DENSE_TOP_K", 8)
    synopsis_lexical_top_k: int = _env_int("HELPMATE_SYNOPSIS_LEXICAL_TOP_K", 8)
    synopsis_fused_top_k: int = _env_int("HELPMATE_SYNOPSIS_FUSED_TOP_K", 5)
    synopsis_section_window: int = _env_int("HELPMATE_SYNOPSIS_SECTION_WINDOW", 4)
    planner_candidate_region_limit: int = _env_int("HELPMATE_PLANNER_CANDIDATE_REGION_LIMIT", 6)
    global_fallback_top_k: int = _env_int("HELPMATE_GLOBAL_FALLBACK_TOP_K", 4)
    reranker_enabled: bool = _env_bool("HELPMATE_RERANKER_ENABLED", True)
    reranker_model: str = os.getenv("HELPMATE_RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
    router_llm_enabled: bool = _env_bool("HELPMATE_ROUTER_LLM_ENABLED", True)
    router_model: str = os.getenv("HELPMATE_ROUTER_MODEL", "gpt-5.4-nano")
    router_confidence_threshold: float = _env_float("HELPMATE_ROUTER_CONFIDENCE_THRESHOLD", 0.6)
    planner_confidence_threshold: float = _env_float("HELPMATE_PLANNER_CONFIDENCE_THRESHOLD", 0.74)
    structure_repair_enabled: bool = _env_bool("HELPMATE_STRUCTURE_REPAIR_ENABLED", True)
    structure_repair_model: str = os.getenv("HELPMATE_STRUCTURE_REPAIR_MODEL", "gpt-5.4-nano")
    structure_repair_confidence_threshold: float = _env_float("HELPMATE_STRUCTURE_REPAIR_CONFIDENCE_THRESHOLD", 0.62)
    embedding_model: str = os.getenv("HELPMATE_EMBEDDING_MODEL", "text-embedding-3-small")
    answer_model: str = os.getenv("HELPMATE_ANSWER_MODEL", "gpt-5.4-mini")
    evidence_selector_enabled: bool = _env_bool("HELPMATE_EVIDENCE_SELECTOR_ENABLED", True)
    evidence_selector_model: str = os.getenv("HELPMATE_EVIDENCE_SELECTOR_MODEL", "gpt-5.4-nano")
    evidence_selector_top_k: int = _env_int("HELPMATE_EVIDENCE_SELECTOR_TOP_K", 4)
    evidence_selector_max_evidence: int = _env_int("HELPMATE_EVIDENCE_SELECTOR_MAX_EVIDENCE", 2)
    evidence_selector_rank_weight: float = _env_float("HELPMATE_EVIDENCE_SELECTOR_RANK_WEIGHT", 0.65)
    evidence_selector_llm_weight: float = _env_float("HELPMATE_EVIDENCE_SELECTOR_LLM_WEIGHT", 0.35)
    evidence_selector_gap_threshold: float = _env_float("HELPMATE_EVIDENCE_SELECTOR_GAP_THRESHOLD", 0.08)
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    retrieval_version: str = os.getenv("HELPMATE_RETRIEVAL_VERSION", "v10")
    generation_version: str = os.getenv("HELPMATE_GENERATION_VERSION", "v5")
    cache_similarity_threshold: float = _env_float("HELPMATE_CACHE_SIMILARITY_THRESHOLD", 0.94)
    weak_evidence_score_threshold: float = _env_float("HELPMATE_WEAK_EVIDENCE_SCORE_THRESHOLD", 0.03)
    unsupported_evidence_score_threshold: float = _env_float("HELPMATE_UNSUPPORTED_EVIDENCE_SCORE_THRESHOLD", 0.012)
    lexical_hit_threshold: float = _env_float("HELPMATE_LEXICAL_HIT_THRESHOLD", 0.02)
    unsupported_lexical_hit_threshold: float = _env_float("HELPMATE_UNSUPPORTED_LEXICAL_HIT_THRESHOLD", 0.005)
    unsupported_content_overlap_threshold: float = _env_float("HELPMATE_UNSUPPORTED_CONTENT_OVERLAP_THRESHOLD", 0.05)

    def ensure_dirs(self) -> None:
        evals_dir = self.docs_dir / "evals"
        for path in (self.data_dir, self.uploads_dir, self.indexes_dir, self.cache_dir, self.docs_dir, evals_dir):
            path.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
