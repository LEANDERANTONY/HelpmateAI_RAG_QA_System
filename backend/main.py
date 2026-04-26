from __future__ import annotations

from functools import lru_cache
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.auth import AuthenticatedUser, require_authenticated_user
from backend.store import build_api_record_store
from src.config import get_settings
from src.evals.report_loader import get_latest_benchmark_report
from src.pipeline import HelpmatePipeline
from src.question_starters import get_question_starters
from src.schemas import AnswerResult, DocumentRecord, IndexRecord


SUPPORTED_UPLOAD_TYPES = {".pdf", ".docx"}
SAMPLE_DOCUMENT_DETAILS = {
    "HealthInsurance_Policy.pdf": {
        "title": "Health Insurance Policy",
        "category": "Policy benchmark",
        "description": "Best quick demo for exclusions, waiting periods, and clause lookup.",
    },
    "Principal-Sample-Life-Insurance-Policy.pdf": {
        "title": "Principal Life Insurance Policy",
        "category": "Policy benchmark",
        "description": "Good for policy-style obligations, cover rules, and definitions.",
    },
    "Final_Thesis_Leander_Antony_A.pdf": {
        "title": "Research Thesis",
        "category": "Thesis benchmark",
        "description": "Useful for section-aware retrieval, summaries, and future-work questions.",
    },
    "pancreas7.pdf": {
        "title": "Pancreas7 Research Paper",
        "category": "Scientific benchmark",
        "description": "Longer scientific paper for harder synthesis and retrieval evaluation.",
    },
    "pancreas8.pdf": {
        "title": "Pancreas8 Research Paper",
        "category": "Scientific benchmark",
        "description": "High-signal paper benchmark where the current stack performs strongly.",
    },
}


class HealthResponse(BaseModel):
    status: str
    app_name: str
    retrieval_version: str
    generation_version: str
    openai_configured: bool
    supported_upload_types: list[str]


class DocumentBundleResponse(BaseModel):
    document: dict[str, Any]
    index: dict[str, Any] | None = None


class CurrentWorkspaceResponse(BaseModel):
    document: dict[str, Any] | None = None
    index: dict[str, Any] | None = None


class StarterQuestionsResponse(BaseModel):
    document_id: str
    document_style: str
    questions: list[str]


class AskRequest(BaseModel):
    document_id: str
    question: str


class AskResponse(BaseModel):
    answer: dict[str, Any]


class BenchmarkResponse(BaseModel):
    report_name: str | None = None
    report_path: str | None = None
    report: dict[str, Any] | None = None


class SampleDocumentResponse(BaseModel):
    slug: str
    file_name: str
    title: str
    category: str
    description: str
    size_bytes: int


app = FastAPI(
    title="HelpmateAI API",
    version="0.1.0",
    description="Thin FastAPI boundary over the existing HelpmateAI RAG core.",
)

settings = get_settings()
cors_origins = list(settings.cors_origins)
allow_all_origins = "*" in cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if allow_all_origins else cors_origins,
    allow_credentials=not allow_all_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@lru_cache
def _settings():
    return get_settings()


@lru_cache
def _pipeline() -> HelpmatePipeline:
    return HelpmatePipeline(_settings())


@lru_cache
def _store() -> Any:
    return build_api_record_store(_settings())


def _require_document(document_id: str) -> DocumentRecord:
    document = _store().get_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return document


def _require_index(document_id: str) -> IndexRecord:
    index_record = _store().get_index(document_id)
    if index_record is None:
        raise HTTPException(
            status_code=409,
            detail="Index has not been built for this document yet.",
        )
    return index_record


def _validate_file_type(file_name: str) -> str:
    suffix = Path(file_name).suffix.lower()
    if suffix not in SUPPORTED_UPLOAD_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Only PDF and DOCX uploads are supported.",
        )
    return suffix


def _document_payload(document: DocumentRecord) -> dict[str, Any]:
    metadata = document.metadata or {}
    safe_metadata = {
        "document_style": metadata.get("document_style"),
        "section_heading": metadata.get("section_heading"),
        "section_kind": metadata.get("section_kind"),
        "content_type": metadata.get("content_type"),
    }
    return {
        "document_id": document.document_id,
        "file_name": document.file_name,
        "file_type": document.file_type,
        "source_path": document.source_path,
        "fingerprint": document.fingerprint,
        "char_count": document.char_count,
        "page_count": document.page_count,
        "metadata": safe_metadata,
    }


WORKSPACE_OWNER_KEY = "_workspace_owner_user_id"
WORKSPACE_LAST_ACTIVITY_KEY = "_workspace_last_activity_at"
WORKSPACE_EXPIRES_AT_KEY = "_workspace_expires_at"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _retention_delta():
    return timedelta(hours=_settings().workspace_retention_hours)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _document_owner_id(document: DocumentRecord) -> str | None:
    return str((document.metadata or {}).get(WORKSPACE_OWNER_KEY) or "") or None


def _document_expires_at(document: DocumentRecord) -> datetime | None:
    return _parse_timestamp((document.metadata or {}).get(WORKSPACE_EXPIRES_AT_KEY))


def _touch_document_workspace(document: DocumentRecord, user: AuthenticatedUser) -> DocumentRecord:
    metadata = dict(document.metadata or {})
    now = _now()
    metadata[WORKSPACE_OWNER_KEY] = user.id
    metadata[WORKSPACE_LAST_ACTIVITY_KEY] = now.isoformat()
    metadata[WORKSPACE_EXPIRES_AT_KEY] = (now + _retention_delta()).isoformat()
    document.metadata = metadata
    return document


def _delete_workspace_records(document: DocumentRecord) -> None:
    index_record = _store().get_index(document.document_id)
    if index_record is not None:
        _pipeline().delete_workspace(document, index_record)
        _store().delete_index(document.document_id)
    _store().delete_document(document.document_id)


def _cleanup_if_expired(document: DocumentRecord) -> bool:
    expires_at = _document_expires_at(document)
    if expires_at is None or expires_at > _now():
        return False
    _delete_workspace_records(document)
    return True


def _find_active_workspace_document(user: AuthenticatedUser) -> DocumentRecord | None:
    active_documents: list[DocumentRecord] = []
    for document in _store().list_documents():
        if _document_owner_id(document) != user.id:
            continue
        if _cleanup_if_expired(document):
            continue
        active_documents.append(document)
    if not active_documents:
        return None
    active_documents.sort(
        key=lambda doc: _parse_timestamp((doc.metadata or {}).get(WORKSPACE_LAST_ACTIVITY_KEY)) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    primary = active_documents[0]
    for stale in active_documents[1:]:
        _delete_workspace_records(stale)
    return primary


def _require_document_for_user(document_id: str, user: AuthenticatedUser) -> DocumentRecord:
    document = _require_document(document_id)
    owner_id = _document_owner_id(document)
    if owner_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found.")
    if _cleanup_if_expired(document):
        raise HTTPException(status_code=410, detail="Your saved workspace expired. Upload the document again to continue.")
    return document


def _save_touched_document(document: DocumentRecord, user: AuthenticatedUser) -> DocumentRecord:
    document = _touch_document_workspace(document, user)
    _store().save_document(document)
    return document


def _sample_dir() -> Path:
    # Use the app root (two levels up from this file: backend/main.py → root)
    # so this resolves correctly regardless of HELPMATE_DATA_DIR on the VPS.
    app_root = Path(__file__).resolve().parent.parent
    return app_root / "static" / "sample_files"


def _build_sample_payload(path: Path) -> SampleDocumentResponse:
    details = SAMPLE_DOCUMENT_DETAILS.get(
        path.name,
        {
            "title": path.stem.replace("_", " "),
            "category": "Sample document",
            "description": "Bundled sample document for frontend demos.",
        },
    )
    return SampleDocumentResponse(
        slug=path.name,
        file_name=path.name,
        title=details["title"],
        category=details["category"],
        description=details["description"],
        size_bytes=path.stat().st_size,
    )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = _settings()
    return HealthResponse(
        status="ok",
        app_name=settings.app_name,
        retrieval_version=settings.retrieval_version,
        generation_version=settings.generation_version,
        openai_configured=bool(settings.openai_api_key),
        supported_upload_types=sorted(SUPPORTED_UPLOAD_TYPES),
    )


@app.post("/documents/upload", response_model=DocumentBundleResponse)
async def upload_document(
    file: UploadFile = File(...),
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> DocumentBundleResponse:
    file_name = Path(file.filename or "document.pdf").name
    suffix = _validate_file_type(file_name)
    existing_document = _find_active_workspace_document(user)
    if existing_document is not None:
        _delete_workspace_records(existing_document)
    target_path = _settings().uploads_dir / file_name
    target_path.write_bytes(await file.read())
    if target_path.suffix.lower() != suffix:
        raise HTTPException(status_code=400, detail="Uploaded file extension mismatch.")

    document = _pipeline().ingest_document(target_path)

    document = _save_touched_document(document, user)
    existing_index = _store().get_index(document.document_id)
    return DocumentBundleResponse(
        document=_document_payload(document),
        index=existing_index.to_dict() if existing_index else None,
    )


@app.get("/samples", response_model=list[SampleDocumentResponse])
def list_sample_documents() -> list[SampleDocumentResponse]:
    sample_dir = _sample_dir()
    if not sample_dir.exists():
        return []
    samples = []
    for path in sorted(sample_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in SUPPORTED_UPLOAD_TYPES:
            samples.append(_build_sample_payload(path))
    return samples


@app.post("/samples/{sample_slug}/load", response_model=DocumentBundleResponse)
def load_sample_document(
    sample_slug: str,
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> DocumentBundleResponse:
    sample_path = (_sample_dir() / Path(sample_slug).name).resolve()
    if sample_path.parent != _sample_dir().resolve() or not sample_path.exists():
        raise HTTPException(status_code=404, detail="Sample document not found.")
    _validate_file_type(sample_path.name)

    existing_document = _find_active_workspace_document(user)
    if existing_document is not None:
        _delete_workspace_records(existing_document)

    document = _pipeline().ingest_document(sample_path)
    index_record = _pipeline().build_or_load_index(document)
    document = _save_touched_document(document, user)
    _store().save_index(index_record)
    return DocumentBundleResponse(
        document=_document_payload(document),
        index=index_record.to_dict(),
    )


@app.get("/workspace/current", response_model=CurrentWorkspaceResponse)
def get_current_workspace(
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> CurrentWorkspaceResponse:
    document = _find_active_workspace_document(user)
    if document is None:
        return CurrentWorkspaceResponse()
    document = _save_touched_document(document, user)
    index_record = _store().get_index(document.document_id)
    return CurrentWorkspaceResponse(
        document=_document_payload(document),
        index=index_record.to_dict() if index_record else None,
    )


@app.post("/documents/{document_id}/index", response_model=DocumentBundleResponse)
def build_or_load_index(
    document_id: str,
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> DocumentBundleResponse:
    document = _require_document_for_user(document_id, user)
    index_record = _pipeline().build_or_load_index(document)
    document = _save_touched_document(document, user)
    _store().save_index(index_record)
    return DocumentBundleResponse(
        document=_document_payload(document),
        index=index_record.to_dict(),
    )


@app.get("/documents/{document_id}", response_model=DocumentBundleResponse)
def get_document(
    document_id: str,
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> DocumentBundleResponse:
    document = _require_document_for_user(document_id, user)
    document = _save_touched_document(document, user)
    index_record = _store().get_index(document_id)
    return DocumentBundleResponse(
        document=_document_payload(document),
        index=index_record.to_dict() if index_record else None,
    )


@app.get("/documents/{document_id}/starters", response_model=StarterQuestionsResponse)
def get_starter_questions(
    document_id: str,
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> StarterQuestionsResponse:
    document = _require_document_for_user(document_id, user)
    _save_touched_document(document, user)
    document_style = (document.metadata or {}).get(
        "document_style",
        "generic_longform",
    )
    return StarterQuestionsResponse(
        document_id=document_id,
        document_style=document_style,
        questions=get_question_starters(document_style),
    )


@app.post("/qa", response_model=AskResponse)
def answer_question(
    payload: AskRequest,
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> AskResponse:
    document = _require_document_for_user(payload.document_id, user)
    index_record = _require_index(payload.document_id)
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")
    answer: AnswerResult = _pipeline().answer_question(
        document,
        index_record,
        question,
    )
    _save_touched_document(document, user)
    return AskResponse(answer=answer.to_dict())


@app.get("/benchmarks/latest", response_model=BenchmarkResponse)
def get_latest_benchmarks() -> BenchmarkResponse:
    report, report_path = get_latest_benchmark_report()
    if report is None or report_path is None:
        return BenchmarkResponse()
    return BenchmarkResponse(
        report_name=report_path.name,
        report_path=str(report_path),
        report=report,
    )
