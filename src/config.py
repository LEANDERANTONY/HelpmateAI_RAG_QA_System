from __future__ import annotations

import os
from dataclasses import dataclass, field
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


def _env_path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    if not value:
        return default
    return Path(value).expanduser()


def _env_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.getenv(name)
    if not value:
        return default
    items = [item.strip() for item in value.split(",")]
    cleaned = tuple(item for item in items if item)
    return cleaned or default


def _env_mapping(name: str) -> dict[str, str]:
    value = os.getenv(name)
    if not value:
        return {}
    pairs: dict[str, str] = {}
    for raw_item in value.split(","):
        item = raw_item.strip()
        if not item or "=" not in item:
            continue
        key, mapped_value = item.split("=", 1)
        key = key.strip()
        mapped_value = mapped_value.strip()
        if key and mapped_value:
            pairs[key] = mapped_value
    return pairs


def _chroma_api_key() -> str | None:
    direct = os.getenv("HELPMATE_CHROMA_API_KEY") or os.getenv("CHROMA_API_KEY")
    if direct:
        return direct.strip() or None

    headers = _env_mapping("HELPMATE_CHROMA_HTTP_HEADERS")
    if "x-chroma-token" in headers:
        return headers["x-chroma-token"]

    authorization = headers.get("Authorization", "")
    bearer_prefix = "Bearer "
    if authorization.startswith(bearer_prefix):
        token = authorization[len(bearer_prefix) :].strip()
        return token or None

    return None


def _env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value if value is not None else default


@dataclass(frozen=True)
class Settings:
    app_name: str = "HelpmateAI"
    app_tagline: str = "Grounded answers for long-form documents."
    docs_dir: Path = field(default_factory=lambda: _env_path("HELPMATE_DOCS_DIR", ROOT_DIR / "docs"))
    data_dir: Path = field(default_factory=lambda: _env_path("HELPMATE_DATA_DIR", ROOT_DIR / "data"))
    uploads_dir: Path = field(default_factory=Path)
    indexes_dir: Path = field(default_factory=Path)
    cache_dir: Path = field(default_factory=Path)
    state_store_backend: str = field(default_factory=lambda: _env_str("HELPMATE_STATE_STORE_BACKEND", "local").strip().lower())
    vector_store_backend: str = field(default_factory=lambda: _env_str("HELPMATE_VECTOR_STORE_BACKEND", "local").strip().lower())
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: _env_list("HELPMATE_CORS_ORIGINS", ("*",)),
    )
    supabase_url: str | None = field(default_factory=lambda: os.getenv("SUPABASE_URL"))
    supabase_key: str | None = field(default_factory=lambda: os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY"))
    supabase_documents_table: str = field(default_factory=lambda: _env_str("HELPMATE_SUPABASE_DOCUMENTS_TABLE", "helpmate_documents"))
    supabase_indexes_table: str = field(default_factory=lambda: _env_str("HELPMATE_SUPABASE_INDEXES_TABLE", "helpmate_indexes"))
    supabase_artifacts_table: str = field(default_factory=lambda: _env_str("HELPMATE_SUPABASE_ARTIFACTS_TABLE", "helpmate_index_artifacts"))
    supabase_run_traces_table: str = field(default_factory=lambda: _env_str("HELPMATE_SUPABASE_RUN_TRACES_TABLE", "helpmate_run_traces"))
    chroma_http_host: str = field(default_factory=lambda: _env_str("HELPMATE_CHROMA_HTTP_HOST", "localhost"))
    chroma_http_port: int = field(default_factory=lambda: _env_int("HELPMATE_CHROMA_HTTP_PORT", 8000))
    chroma_http_ssl: bool = field(default_factory=lambda: _env_bool("HELPMATE_CHROMA_HTTP_SSL", False))
    chroma_http_tenant: str = field(default_factory=lambda: _env_str("HELPMATE_CHROMA_HTTP_TENANT", "default_tenant"))
    chroma_http_database: str = field(default_factory=lambda: _env_str("HELPMATE_CHROMA_HTTP_DATABASE", "default_database"))
    chroma_http_headers: dict[str, str] = field(
        default_factory=lambda: _env_mapping("HELPMATE_CHROMA_HTTP_HEADERS"),
    )
    chroma_api_key: str | None = field(default_factory=_chroma_api_key)
    chroma_upsert_batch_size: int = field(default_factory=lambda: _env_int("HELPMATE_CHROMA_UPSERT_BATCH_SIZE", 250))
    pdf_extractor: str = field(default_factory=lambda: _env_str("HELPMATE_PDF_EXTRACTOR", "auto").strip().lower())
    docx_extractor: str = field(default_factory=lambda: _env_str("HELPMATE_DOCX_EXTRACTOR", "auto").strip().lower())
    index_schema_version: str = os.getenv("HELPMATE_INDEX_SCHEMA_VERSION", "v16")
    chunk_size: int = _env_int("HELPMATE_CHUNK_SIZE", 1200)
    chunk_overlap: int = _env_int("HELPMATE_CHUNK_OVERLAP", 240)
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
    planner_candidate_region_limit: int = _env_int("HELPMATE_PLANNER_CANDIDATE_REGION_LIMIT", 10)
    global_fallback_top_k: int = _env_int("HELPMATE_GLOBAL_FALLBACK_TOP_K", 3)
    retrieval_orchestrator_enabled: bool = _env_bool("HELPMATE_RETRIEVAL_ORCHESTRATOR_ENABLED", True)
    retrieval_orchestrator_model: str = os.getenv("HELPMATE_RETRIEVAL_ORCHESTRATOR_MODEL", "gpt-5.4-nano")
    retrieval_orchestrator_max_sections: int = _env_int("HELPMATE_RETRIEVAL_ORCHESTRATOR_MAX_SECTIONS", 120)
    retrieval_orchestrator_min_confidence: float = _env_float("HELPMATE_RETRIEVAL_ORCHESTRATOR_MIN_CONFIDENCE", 0.55)
    planner_llm_enabled: bool = _env_bool("HELPMATE_PLANNER_LLM_ENABLED", True)
    planner_model: str = os.getenv("HELPMATE_PLANNER_MODEL", "gpt-5.4-nano")
    reranker_enabled: bool = _env_bool("HELPMATE_RERANKER_ENABLED", True)
    reranker_model: str = os.getenv("HELPMATE_RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
    router_llm_enabled: bool = _env_bool("HELPMATE_ROUTER_LLM_ENABLED", True)
    router_model: str = os.getenv("HELPMATE_ROUTER_MODEL", "gpt-5.4-nano")
    router_confidence_threshold: float = _env_float("HELPMATE_ROUTER_CONFIDENCE_THRESHOLD", 0.62)
    planner_confidence_threshold: float = _env_float("HELPMATE_PLANNER_CONFIDENCE_THRESHOLD", 0.7)
    structure_repair_enabled: bool = _env_bool("HELPMATE_STRUCTURE_REPAIR_ENABLED", True)
    structure_repair_model: str = os.getenv("HELPMATE_STRUCTURE_REPAIR_MODEL", "gpt-5.4-nano")
    structure_repair_confidence_threshold: float = _env_float("HELPMATE_STRUCTURE_REPAIR_CONFIDENCE_THRESHOLD", 0.62)
    structure_repair_require_header_dominated: bool = _env_bool("HELPMATE_STRUCTURE_REPAIR_REQUIRE_HEADER_DOMINATED", True)
    chunk_semantics_enabled: bool = _env_bool("HELPMATE_CHUNK_SEMANTICS_ENABLED", False)
    chunk_semantics_model: str = os.getenv("HELPMATE_CHUNK_SEMANTICS_MODEL", "gpt-5.4-nano")
    chunk_semantics_max_review_chunks: int = _env_int("HELPMATE_CHUNK_SEMANTICS_MAX_REVIEW_CHUNKS", 12)
    synopsis_semantics_enabled: bool = _env_bool("HELPMATE_SYNOPSIS_SEMANTICS_ENABLED", False)
    synopsis_semantics_model: str = os.getenv("HELPMATE_SYNOPSIS_SEMANTICS_MODEL", "gpt-5.4-nano")
    synopsis_semantics_max_sections: int = _env_int("HELPMATE_SYNOPSIS_SEMANTICS_MAX_SECTIONS", 6)
    synopsis_semantics_gate_mode: str = _env_str("HELPMATE_SYNOPSIS_SEMANTICS_GATE_MODE", "targeted").strip().lower()
    embedding_model: str = os.getenv("HELPMATE_EMBEDDING_MODEL", "text-embedding-3-small")
    answer_model: str = os.getenv("HELPMATE_ANSWER_MODEL", "gpt-5.4-mini")
    evidence_selector_enabled: bool = _env_bool("HELPMATE_EVIDENCE_SELECTOR_ENABLED", True)
    evidence_selector_model: str = os.getenv("HELPMATE_EVIDENCE_SELECTOR_MODEL", "gpt-5.4-nano")
    evidence_selector_top_k: int = _env_int("HELPMATE_EVIDENCE_SELECTOR_TOP_K", 4)
    evidence_selector_max_evidence: int = _env_int("HELPMATE_EVIDENCE_SELECTOR_MAX_EVIDENCE", 2)
    evidence_selector_prune: bool = _env_bool("HELPMATE_EVIDENCE_SELECTOR_PRUNE", False)
    evidence_selector_rank_weight: float = _env_float("HELPMATE_EVIDENCE_SELECTOR_RANK_WEIGHT", 0.25)
    evidence_selector_llm_weight: float = _env_float("HELPMATE_EVIDENCE_SELECTOR_LLM_WEIGHT", 0.75)
    evidence_selector_gap_threshold: float = _env_float("HELPMATE_EVIDENCE_SELECTOR_GAP_THRESHOLD", 0.08)
    evidence_selector_trigger_weak_evidence: bool = _env_bool("HELPMATE_EVIDENCE_SELECTOR_TRIGGER_WEAK_EVIDENCE", False)
    evidence_selector_trigger_spread: bool = _env_bool("HELPMATE_EVIDENCE_SELECTOR_TRIGGER_SPREAD", True)
    evidence_selector_trigger_ambiguity: bool = _env_bool("HELPMATE_EVIDENCE_SELECTOR_TRIGGER_AMBIGUITY", False)
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    retrieval_version: str = os.getenv("HELPMATE_RETRIEVAL_VERSION", "v17")
    generation_version: str = os.getenv("HELPMATE_GENERATION_VERSION", "v7")
    cache_similarity_threshold: float = _env_float("HELPMATE_CACHE_SIMILARITY_THRESHOLD", 0.94)
    weak_evidence_score_threshold: float = _env_float("HELPMATE_WEAK_EVIDENCE_SCORE_THRESHOLD", 0.03)
    unsupported_evidence_score_threshold: float = _env_float("HELPMATE_UNSUPPORTED_EVIDENCE_SCORE_THRESHOLD", 0.012)
    lexical_hit_threshold: float = _env_float("HELPMATE_LEXICAL_HIT_THRESHOLD", 0.02)
    unsupported_lexical_hit_threshold: float = _env_float("HELPMATE_UNSUPPORTED_LEXICAL_HIT_THRESHOLD", 0.005)
    unsupported_content_overlap_threshold: float = _env_float("HELPMATE_UNSUPPORTED_CONTENT_OVERLAP_THRESHOLD", 0.05)
    workspace_retention_hours: int = _env_int("HELPMATE_WORKSPACE_RETENTION_HOURS", 24)

    def __post_init__(self) -> None:
        uploads_dir = _env_path("HELPMATE_UPLOADS_DIR", self.data_dir / "uploads")
        indexes_dir = _env_path("HELPMATE_INDEXES_DIR", self.data_dir / "indexes")
        cache_dir = _env_path("HELPMATE_CACHE_DIR", self.data_dir / "cache")
        object.__setattr__(self, "uploads_dir", uploads_dir)
        object.__setattr__(self, "indexes_dir", indexes_dir)
        object.__setattr__(self, "cache_dir", cache_dir)

    def ensure_dirs(self) -> None:
        evals_dir = self.docs_dir / "evals"
        for path in (self.data_dir, self.uploads_dir, self.indexes_dir, self.cache_dir, self.docs_dir, evals_dir):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def uses_supabase_state(self) -> bool:
        return self.state_store_backend == "supabase"

    @property
    def uses_chroma_http(self) -> bool:
        return self.vector_store_backend in {"chroma_http", "chroma_cloud", "http"}


def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
