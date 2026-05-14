from __future__ import annotations

import logging
import tempfile
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from backend.auth import AuthenticatedUser, require_authenticated_user
from backend.file_storage import FileStorage, build_file_storage
from backend.quota import (
    UPGRADE_URL,
    check_content_length_present,
    check_doc_count_cap,
    check_file_size_cap,
    check_premium_quota,
    check_question_quota,
)
from backend.quota_store import QuotaStore, build_quota_store
from backend.store import build_api_record_store
from backend.tiers import RETENTION_UNBOUNDED, TIER_LIMITS, resolve_user_tier
from src.config import get_settings
from src.evals.report_loader import get_latest_benchmark_report
from src.pipeline import HelpmatePipeline
from src.question_starters import get_question_starters
from src.schemas import AnswerResult, DocumentRecord, IndexRecord


logger = logging.getLogger(__name__)


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


class QuotaCountInfo(BaseModel):
    """`used` is the current value, `limit` is the cap. -1 limit
    signals "no practical cap" (the soft ceiling at 1000 docs is
    reported as 1000, never -1 — only retention uses -1)."""

    used: int
    limit: int


class WorkspaceQuotaResponse(BaseModel):
    """Snapshot of the signed-in user's quota state.

    The frontend uses this to:
      • drive the Premium toggle (disable when premium_available=False,
        show used/limit when True)
      • populate per-quota indicators (questions remaining etc.)
      • render upgrade prompts pointing at upgrade_url

    period_start is the first-of-month UTC date for the current
    counter window — useful for "resets in N days" copy.
    """

    tier: str
    period_start: str
    questions: QuotaCountInfo
    premium: QuotaCountInfo
    premium_available: bool
    documents: QuotaCountInfo
    upgrade_url: str


class StarterQuestionsResponse(BaseModel):
    document_id: str
    document_style: str
    questions: list[str]


class AskRequest(BaseModel):
    document_id: str
    question: str
    # Opt-in per-question. When True and the tier supports it
    # (premium_model in TIER_LIMITS), /qa routes to gpt-5.5 and
    # decrements both the standard and premium counters. Never
    # trust this flag from the client — the backend re-validates
    # tier eligibility on every call (free tier always rejects).
    premium: bool = False


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


@lru_cache
def _quota_store() -> QuotaStore:
    # Local backend for HELPMATE_STATE_STORE_BACKEND=local (JSON file at
    # data/api_state/quota_counters.json), Supabase otherwise (atomic RPC).
    return build_quota_store(_settings())


@lru_cache
def _file_storage() -> FileStorage:
    # Selected by HELPMATE_FILE_STORAGE_BACKEND. Local is the default;
    # supabase is the production target. See backend/file_storage.py for
    # the strategy and the storage semantics of DocumentRecord.source_path
    # / viewable_pdf_path under each backend.
    return build_file_storage(_settings())


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
    """Legacy fallback delta — the ephemeral-workspace clock from the
    env var. Used by callers that don't have a user (eval scripts,
    sample-loader paths). The user-driven /qa + upload paths use
    `_retention_delta_for_user` instead, which picks the per-tier
    duration. See docs/tier-enforcement-flags.md.
    """
    return timedelta(hours=_settings().workspace_retention_hours)


def _retention_delta_for_user(user: AuthenticatedUser) -> timedelta | None:
    """Per-tier retention duration applied at workspace-touch time.

    Returns None for unbounded retention (Business tier) — callers
    interpret None as "don't set expires_at", which keeps the sweeper
    from ever deleting the workspace on age grounds.

    Falls back to the env-var ephemeral delta only as a defensive
    catch — every tier in TIER_LIMITS has retention_days set, so the
    fallback path is unreachable under normal config.
    """
    tier = resolve_user_tier(user)
    days = TIER_LIMITS[tier]["retention_days"]
    if days == RETENTION_UNBOUNDED:
        return None
    if days <= 0:
        return _retention_delta()
    return timedelta(days=days)


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
    """Stamp owner + activity + (tier-aware) expires_at on the document.

    Retention is per-tier from Step 6:
      Free      → 30 days of inactivity before the sweeper deletes
      Pro       → 365 days
      Business  → unbounded (expires_at field is REMOVED entirely so
                  the sweeper's `expires_at < now` check never fires)

    Removing the field for Business — rather than setting a far-future
    sentinel — keeps the sweep query simple and means a tier downgrade
    later (Business → Pro) doesn't strand a 9999-year deadline on the
    record. The next touch under the new tier resets the field cleanly.
    """
    metadata = dict(document.metadata or {})
    now = _now()
    metadata[WORKSPACE_OWNER_KEY] = user.id
    metadata[WORKSPACE_LAST_ACTIVITY_KEY] = now.isoformat()
    delta = _retention_delta_for_user(user)
    if delta is None:
        # Unbounded retention (Business). Strip any stale expires_at
        # left over from a previous touch under a different tier so
        # the sweeper treats this as never-expires.
        metadata.pop(WORKSPACE_EXPIRES_AT_KEY, None)
    else:
        metadata[WORKSPACE_EXPIRES_AT_KEY] = (now + delta).isoformat()
    document.metadata = metadata
    return document


def _delete_workspace_records(document: DocumentRecord) -> None:
    index_record = _store().get_index(document.document_id)
    if index_record is not None:
        # pipeline.delete_workspace cleans up local files when source_path /
        # viewable_pdf_path point at absolute filesystem paths. On the
        # supabase backend those fields hold bucket keys instead, so the
        # local unlinks become no-ops (Path("user-x/doc.pdf").exists() is
        # False) — we follow up below with the storage-aware cleanup that
        # actually removes the bucket objects.
        _pipeline().delete_workspace(document, index_record)
        _store().delete_index(document.document_id)
    _delete_storage_files(document)
    _store().delete_document(document.document_id)


def _delete_storage_files(document: DocumentRecord) -> None:
    """Remove the source + viewable PDF from the configured FileStorage.

    For the local backend the pipeline already unlinked these files; for
    the supabase backend this is where the bucket objects actually get
    deleted. Best-effort — we don't want a Supabase outage to block the
    metadata cleanup, since orphaned bucket objects can be garbage-
    collected by the maintenance sweeper later.
    """
    storage = _file_storage()
    keys: set[str] = set()
    if document.source_path:
        keys.add(document.source_path)
    if document.viewable_pdf_path:
        keys.add(document.viewable_pdf_path)
    for key in keys:
        try:
            storage.delete(key)
        except Exception as exc:
            logger.warning(
                "FileStorage.delete failed for %s (%s): %s",
                document.document_id,
                key,
                exc,
            )


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


def _count_active_documents(user: AuthenticatedUser) -> int:
    """Number of un-expired documents owned by `user`.

    Read-only: unlike _find_active_workspace_document this does NOT
    side-effect (no cleanup of expired records). The quota gate calls
    this BEFORE the existing-doc deletion in the upload flow, so a
    user re-uploading their single workspace doc sees count=1 — the
    gate uses >= comparison so cap=3 still allows the re-upload.

    Reads through the store on every call. Fine at the current scale
    (one workspace per user, dozens of users). If multi-doc lands
    and per-user doc counts climb into the hundreds, consider an
    indexed count query at the store layer.
    """
    active = 0
    for document in _store().list_documents():
        if _document_owner_id(document) != user.id:
            continue
        expires_at = _document_expires_at(document)
        if expires_at is not None and expires_at <= _now():
            continue
        active += 1
    return active


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


def _materialize_uploads_to_storage(
    document: DocumentRecord, user: AuthenticatedUser
) -> None:
    """Push the ingested local files into the configured FileStorage and
    rewrite document.source_path / viewable_pdf_path to the returned storage
    keys.

    For the local backend this is a no-op — the pipeline has already
    written files to `uploads_dir/{document_id}{ext}` which IS the canonical
    storage location, and source_path already holds those absolute paths.

    For the Supabase backend it uploads both files (source + viewable PDF
    rendition) to the bucket under `{user_id}/{document_id}{ext}`, replaces
    the path fields with the bucket keys, and removes the local copies so
    the VPS uploads_dir stays effectively empty.

    Called once at the end of the upload pipeline, after
    `pipeline.ingest_document()` has produced both files. Idempotent in the
    sense that if both fields already point at storage keys (e.g. on a
    re-ingest that hit the no-op normalize_upload_paths fast path), the
    re-upload is upsert-safe and the cleanup just no-ops.
    """
    settings = _settings()
    if not settings.uses_supabase_storage:
        return

    storage = _file_storage()
    source_local = Path(document.source_path)
    viewable_raw = document.viewable_pdf_path

    # Upload the source file.
    if source_local.exists() and source_local.is_file():
        source_key = storage.save_from_path(
            owner_id=user.id,
            document_id=document.document_id,
            source=source_local,
        )
    else:
        logger.warning(
            "Source file missing for %s at %s — cannot materialize to "
            "Supabase Storage. Document record will retain the local path.",
            document.document_id,
            source_local,
        )
        return

    # Decide whether the viewable PDF is a distinct artifact (DOCX upload)
    # or just an alias of the source (PDF upload).
    viewable_key: str | None
    viewable_local: Path | None = None
    if viewable_raw:
        viewable_local = Path(viewable_raw)
        try:
            same_file = viewable_local.resolve() == source_local.resolve()
        except OSError:
            same_file = viewable_raw == document.source_path
        if same_file:
            viewable_key = source_key
            viewable_local = None  # No distinct cleanup needed.
        elif viewable_local.exists() and viewable_local.is_file():
            viewable_key = storage.save_from_path(
                owner_id=user.id,
                document_id=document.document_id,
                source=viewable_local,
            )
        else:
            # The pipeline reported a viewable path but the file isn't on
            # disk — DOCX conversion likely failed silently. Drop the
            # reference so the frontend falls through to the download
            # affordance instead of 404'ing on a phantom key.
            viewable_key = None
            viewable_local = None
    else:
        viewable_key = None

    document.source_path = source_key
    document.viewable_pdf_path = viewable_key

    # Clean up the local copies — they're now in Supabase. Best-effort:
    # the upload already succeeded so a stray local file just wastes a
    # bit of VPS disk until the workspace sweeper finds it.
    for path in (source_local, viewable_local):
        if path is None:
            continue
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            logger.warning(
                "Failed to remove local copy of %s after Supabase upload: %s",
                path,
                exc,
            )


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
    request: Request,
    file: UploadFile = File(...),
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> DocumentBundleResponse | JSONResponse:
    file_name = Path(file.filename or "document.pdf").name
    suffix = _validate_file_type(file_name)

    # Tier-enforcement gates run BEFORE the existing-workspace deletion
    # and before any file bytes touch disk / the pipeline, so a rejected
    # upload leaves the user's existing workspace + storage untouched.
    # Each check returns a JSONResponse on failure; we forward it directly.
    tier = resolve_user_tier(user)
    limits = TIER_LIMITS[tier]

    # Gate 1 — Content-Length header must be present. Missing header
    # signals chunked transfer-encoding (or a misbehaving client) and
    # would let the file-size cap be trivially bypassed.
    content_length_raw = request.headers.get("content-length")
    try:
        content_length = int(content_length_raw) if content_length_raw is not None else None
    except ValueError:
        content_length = None
    header_response = check_content_length_present(
        content_length=content_length,
        tier=tier,
        limits=limits,
    )
    if header_response is not None:
        return header_response

    # Gate 2 — file body size vs tier cap. UploadFile.size is the
    # actual body size after multipart parsing, not the envelope-
    # inflated Content-Length. See check_file_size_cap's docstring.
    file_size = file.size or 0
    size_response = check_file_size_cap(
        file_size=file_size,
        tier=tier,
        limits=limits,
    )
    if size_response is not None:
        return size_response

    # Gate 3 — active document count vs tier cap. Counts BEFORE the
    # existing-doc deletion below, so a re-upload of the user's single
    # workspace doc sees count=1 (allowed under cap=3 for free).
    count_response = check_doc_count_cap(
        active_count=_count_active_documents(user),
        tier=tier,
        limits=limits,
    )
    if count_response is not None:
        return count_response

    existing_document = _find_active_workspace_document(user)
    if existing_document is not None:
        _delete_workspace_records(existing_document)
    # The bytes always land on local disk first because the ingestion
    # pipeline (parsing, DOCX→PDF conversion via LibreOffice, chunking)
    # needs a Path it can read with stdlib tooling. On the local backend
    # this IS the canonical storage location; on the supabase backend
    # we'll upload to the bucket and clean up locally via
    # _materialize_uploads_to_storage below.
    target_path = _settings().uploads_dir / file_name
    target_path.write_bytes(await file.read())
    if target_path.suffix.lower() != suffix:
        raise HTTPException(status_code=400, detail="Uploaded file extension mismatch.")

    document = _pipeline().ingest_document(target_path)

    # Hand the ingested files off to the configured storage backend.
    # No-op for local; uploads to Supabase bucket and rewrites the
    # source_path / viewable_pdf_path fields to bucket keys otherwise.
    _materialize_uploads_to_storage(document, user)

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
    # Sample documents go through the same storage materialization as
    # uploads — on the supabase backend the sample's bytes end up in the
    # user's per-bucket prefix, so they own their own copy and the read
    # path doesn't have to special-case them.
    _materialize_uploads_to_storage(document, user)
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


@app.get("/workspace/quota", response_model=WorkspaceQuotaResponse)
def get_workspace_quota(
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> WorkspaceQuotaResponse:
    """Per-user quota snapshot for the current calendar month.

    Read by the frontend on workspace mount and after each /qa
    response to keep the Premium toggle's used/limit indicator in
    sync. Returns the user's tier so the UI can disable premium for
    free users without a second lookup.

    No quota gate runs here — pure read. Cheap and safe to call
    frequently (single counter read + single doc-count scan).
    """
    tier = resolve_user_tier(user)
    limits = TIER_LIMITS[tier]
    counter = _quota_store().get_counter(user.id)
    # current_period_start lives in quota_store; the counter row
    # exists at most one per (user, period_start). Surface the
    # period date so the frontend can render "resets on X".
    from backend.quota_store import current_period_start

    return WorkspaceQuotaResponse(
        tier=tier,
        period_start=current_period_start().isoformat(),
        questions=QuotaCountInfo(
            used=counter.questions,
            limit=limits["questions_per_month"],
        ),
        premium=QuotaCountInfo(
            used=counter.premium,
            limit=limits["premium_answers_per_month"],
        ),
        premium_available=limits["premium_model"] is not None,
        documents=QuotaCountInfo(
            used=_count_active_documents(user),
            limit=limits["doc_cap"],
        ),
        upgrade_url=UPGRADE_URL,
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
    metadata = document.metadata or {}
    document_style = metadata.get("document_style", "generic_longform")
    # Doc-tuned questions written by DocumentClassifierService during indexing
    # win when they exist. Otherwise fall back to the per-style deterministic
    # pack so the endpoint always returns three usable items.
    custom = metadata.get("document_starter_questions")
    questions: list[str] = []
    if isinstance(custom, list):
        questions = [str(item).strip() for item in custom if isinstance(item, str) and item.strip()]
    if len(questions) != 3:
        questions = get_question_starters(document_style)
    return StarterQuestionsResponse(
        document_id=document_id,
        document_style=document_style,
        questions=questions,
    )


_INLINE_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
}


@app.get("/documents/{document_id}/file")
def get_document_file(
    document_id: str,
    download: int = 0,
    user: AuthenticatedUser = Depends(require_authenticated_user),
):
    """Serve the document file for inline viewing or download.

    Two distinct files live behind a document_id once ingest finishes:

    - `source_path`   the original upload (`.pdf` or `.docx`), renamed to
                      `{document_id}{ext}` for collision-safe storage.
                      Returned for `?download=1` so the user gets the file
                      in its original format.
    - `viewable_pdf_path`  a PDF rendition the in-app viewer can render
                      with PDF.js. For PDF uploads this aliases the source.
                      For DOCX uploads it's a LibreOffice-produced sibling
                      file. Returned for the default (inline) branch.

    Backward compatibility: documents indexed before viewable_pdf_path
    existed don't carry it. For those we fall back to the source file
    when its extension is `.pdf`; DOCX-only legacy records get a 415 on
    the inline path so the frontend can render a "download to view"
    affordance instead.

    Storage backend behavior:
      • LOCAL    Stream via Starlette's FileResponse. Range requests
                 give 206 Partial Content automatically; PDF.js relies
                 on this for incremental page rendering.
      • SUPABASE Issue a 302 redirect to a short-lived signed URL on
                 the Supabase Storage CDN. The browser fetches the PDF
                 directly from Supabase with Range support — zero
                 bytes pass through our VPS, and we burn no upstream
                 bandwidth on PDF reads.
    """
    document = _require_document_for_user(document_id, user)
    storage = _file_storage()
    base_headers = {"Cache-Control": "private, max-age=3600"}

    # Pick the right storage key for this branch.
    if download:
        # Download always serves the ORIGINAL source format. A user who
        # uploaded a DOCX should get their DOCX back, not the PDF rendition.
        key = document.source_path
        force_download = True
        download_filename: str | None = document.file_name
    else:
        # Inline branch: prefer the viewable PDF, fall back to source for
        # legacy records (pre-Stage-2 docs have viewable_pdf_path=None).
        key = getattr(document, "viewable_pdf_path", None) or document.source_path
        force_download = False
        download_filename = None
        if Path(key).suffix.lower() != ".pdf":
            # Inline rendering needs a PDF rendition. Only fires for legacy
            # DOCX docs that predate viewable_pdf_path; the frontend reads
            # 415 as "switch to download affordance".
            raise HTTPException(
                status_code=415,
                detail="Inline viewing requires a PDF rendition; use ?download=1 to fetch the source.",
            )

    if not key:
        raise HTTPException(status_code=404, detail="Document file is missing.")

    # Supabase backend: 302 to a signed URL. The Supabase CDN handles
    # Range requests natively, so PDF.js streaming Just Works.
    signed_url = storage.get_signed_url(
        key,
        expires_in=3600,
        download=force_download,
        filename=download_filename,
    )
    if signed_url:
        return RedirectResponse(
            url=signed_url,
            status_code=302,
            headers=base_headers,
        )

    # Local backend: stream from disk. uploads_dir fallback handles the
    # case where the stored absolute path is stale (Supabase metadata
    # shared between a prod box and a dev box, or the uploads volume
    # remounted at a new path) — we recompute the canonical path from
    # document_id + suffix and try that.
    primary = Path(key)
    if primary.exists() and primary.is_file():
        local_path = primary
    else:
        fallback = settings.uploads_dir / f"{document.document_id}{primary.suffix}"
        if fallback.exists() and fallback.is_file():
            local_path = fallback
        else:
            raise HTTPException(
                status_code=404, detail="Document file is missing on disk."
            )

    if download:
        media_type = _INLINE_MEDIA_TYPES.get(
            local_path.suffix.lower(), "application/octet-stream"
        )
        return FileResponse(
            path=local_path,
            media_type=media_type,
            filename=document.file_name,
            headers=base_headers,
        )
    return FileResponse(
        path=local_path,
        media_type="application/pdf",
        headers={**base_headers, "Content-Disposition": "inline"},
    )


@app.post("/qa", response_model=AskResponse)
def answer_question(
    payload: AskRequest,
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> AskResponse | JSONResponse:
    document = _require_document_for_user(payload.document_id, user)
    index_record = _require_index(payload.document_id)
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    # Quota gates run BEFORE the pipeline so we never pay OpenAI/Chroma
    # cost just to deny the answer.
    #
    # Pre-check is read-only; post-increment uses the atomic RPC (on
    # Supabase) so two concurrent /qa requests can't both squeeze
    # through the pre-check at N-1 and both run pipeline. Pipeline
    # failures propagate before the increment runs — a failed answer
    # doesn't consume the user's quota.
    #
    # Branch order:
    #   1. If premium=True, check premium-availability + premium quota
    #      FIRST. A free-tier user opting into premium gets a clear
    #      "premium unavailable" 402, not a "question quota" 402.
    #   2. Either way, the standard question quota also applies. Brief:
    #      "BOTH count toward standard AND charge a premium credit,
    #      since the underlying call still happens."
    tier = resolve_user_tier(user)
    limits = TIER_LIMITS[tier]
    counter = _quota_store().get_counter(user.id)

    if payload.premium:
        premium_response = check_premium_quota(
            premium_used=counter.premium,
            tier=tier,
            limits=limits,
        )
        if premium_response is not None:
            return premium_response

    quota_response = check_question_quota(
        questions_used=counter.questions,
        tier=tier,
        limits=limits,
    )
    if quota_response is not None:
        return quota_response

    # Tier-aware model selection. Premium opt-in routes to the tier's
    # premium_model (gpt-5.5) when available; otherwise the tier's
    # default answer_model (free → nano, pro/business → mini). The
    # pipeline's cache key incorporates the active model so the same
    # question never shares a cached answer across model variants.
    active_model = (
        limits["premium_model"] if payload.premium and limits["premium_model"]
        else limits["answer_model"]
    )
    answer: AnswerResult = _pipeline().answer_question(
        document,
        index_record,
        question,
        model_override=active_model,
    )

    # Increment AFTER successful generation. Pipeline raised → we
    # return here via the exception path with no increment.
    #
    # Premium calls increment BOTH counters (per spec): the standard
    # question counter still ticks because the LLM call happened, AND
    # the premium counter gets one charge for using the upgraded
    # model. This makes total spend transparent on a single counter
    # while still letting the premium cap function as the smaller
    # bound on premium-model use specifically.
    _quota_store().increment_questions(user.id)
    if payload.premium:
        _quota_store().increment_premium(user.id)

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
