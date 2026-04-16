from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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


def _sample_dir() -> Path:
    return _settings().data_dir.parent / "static" / "sample_files"


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
async def upload_document(file: UploadFile = File(...)) -> DocumentBundleResponse:
    file_name = Path(file.filename or "document.pdf").name
    suffix = _validate_file_type(file_name)
    target_path = _settings().uploads_dir / file_name
    target_path.write_bytes(await file.read())
    if target_path.suffix.lower() != suffix:
        raise HTTPException(status_code=400, detail="Uploaded file extension mismatch.")

    document = _pipeline().ingest_document(target_path)

    _store().save_document(document)
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
def load_sample_document(sample_slug: str) -> DocumentBundleResponse:
    sample_path = (_sample_dir() / Path(sample_slug).name).resolve()
    if sample_path.parent != _sample_dir().resolve() or not sample_path.exists():
        raise HTTPException(status_code=404, detail="Sample document not found.")
    _validate_file_type(sample_path.name)

    document = _pipeline().ingest_document(sample_path)
    index_record = _pipeline().build_or_load_index(document)
    _store().save_document(document)
    _store().save_index(index_record)
    return DocumentBundleResponse(
        document=_document_payload(document),
        index=index_record.to_dict(),
    )


@app.post("/documents/{document_id}/index", response_model=DocumentBundleResponse)
def build_or_load_index(document_id: str) -> DocumentBundleResponse:
    document = _require_document(document_id)
    index_record = _pipeline().build_or_load_index(document)
    _store().save_index(index_record)
    return DocumentBundleResponse(
        document=_document_payload(document),
        index=index_record.to_dict(),
    )


@app.get("/documents/{document_id}", response_model=DocumentBundleResponse)
def get_document(document_id: str) -> DocumentBundleResponse:
    document = _require_document(document_id)
    index_record = _store().get_index(document_id)
    return DocumentBundleResponse(
        document=_document_payload(document),
        index=index_record.to_dict() if index_record else None,
    )


@app.get("/documents/{document_id}/starters", response_model=StarterQuestionsResponse)
def get_starter_questions(document_id: str) -> StarterQuestionsResponse:
    document = _require_document(document_id)
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
def answer_question(payload: AskRequest) -> AskResponse:
    document = _require_document(payload.document_id)
    index_record = _require_index(payload.document_id)
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")
    answer: AnswerResult = _pipeline().answer_question(
        document,
        index_record,
        question,
    )
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
