from __future__ import annotations

import asyncio
import logging
import tempfile
from contextlib import asynccontextmanager
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from backend.auth import AuthenticatedUser, require_authenticated_user
from backend.billing_routes import router as billing_router
from backend.feedback_store import (
    FeedbackStore,
    FeedbackValidationError,
    build_feedback_store,
)
from backend.file_storage import FileStorage, build_file_storage
from backend.observability import (
    capture_event,
    initialize_observability,
    shutdown_observability,
)
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

# Accepted audio mime types for /transcribe. The MediaRecorder API on
# the major browsers produces audio/webm by default; Safari emits
# audio/mp4. We let the OpenAI Whisper API decide format compatibility
# rather than gating tightly here — anything outside this list is
# rejected with a clear 400 before we burn tokens on it.
SUPPORTED_TRANSCRIBE_AUDIO_TYPES = {
    "audio/webm",
    "audio/mp4",
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/ogg",
    "audio/x-m4a",
    "audio/m4a",
}

# Hard upper bound on the body size of an uploaded audio clip. Whisper
# itself caps at 25 MB; we mirror that so a misconfigured client (or a
# malicious one streaming a 1 GB blob) gets a 413 before we touch
# OpenAI. The default MediaRecorder produces ~16 KB/sec at the default
# bitrate, so 25 MB is roughly 25 minutes of recording — well past the
# "ask your PDF" use case (single multi-paragraph question).
TRANSCRIBE_MAX_FILE_BYTES = 25 * 1024 * 1024
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
    # Per-tier workspace retention window, in days (Free 30, Pro 365).
    # -1 = unbounded (Business never auto-deletes). Surfaced so the UI
    # can proactively tell users their workspace isn't kept forever —
    # retention was previously a silent background sweep (P2).
    retention_days: int
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


class FeedbackRequest(BaseModel):
    """Inbound feedback payload.

    ``trace_id`` is optional — feedback can come from surfaces that
    aren't a tracked /qa run (eg. a "copy citation" pill in the future).
    ``rating`` is the required signal; ``comment`` is the optional
    free-text follow-up. ``surface`` lets us slice the table by where
    feedback was issued without baking that into the URL path.
    """

    trace_id: str | None = None
    rating: str  # validated by FeedbackStore against {'up','down'}
    surface: str = "answer"
    comment: str = ""


class FeedbackResponse(BaseModel):
    """Echo back the persisted row's PK + timestamp so the optimistic
    UI on the frontend can swap in the real id and replace its
    locally-generated placeholder."""

    feedback_id: str
    created_at: str


class TranscribeResponse(BaseModel):
    """Whisper transcription result.

    ``text`` is the transcript (already stripped of leading/trailing
    whitespace). ``duration_seconds`` is Whisper's reported audio
    duration when the SDK surfaces it via ``verbose_json``; otherwise
    a wall-clock measurement of how long the API call took. The
    frontend uses the value purely for telemetry / "you spoke for X
    seconds" affordances — never as a billing signal."""

    text: str
    duration_seconds: float


settings = get_settings()

# Initialize Sentry + PostHog BEFORE ``FastAPI()`` so Sentry's ASGI
# middleware wraps the app instance at construction time. Both are
# no-ops when their respective DSN / API key env vars are unset, so
# the order is safe in environments that haven't wired the
# integration on yet (local dev, CI, the test suite). See
# ``backend/observability.py`` for the bootstrap details.
initialize_observability(settings)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # ``yield`` is the boundary: startup happens above (we already
    # initialized observability at import time, so there's no setup
    # work to do here), shutdown happens below.
    try:
        yield
    finally:
        # Flush the PostHog buffer on graceful termination. PostHog also
        # registers its own atexit handler, but explicit drain via lifespan
        # ensures buffered events leave the process even when the worker
        # is killed by a signal that the interpreter atexit chain doesn't
        # reach (eg. SIGTERM on Vercel / a container redeploy).
        shutdown_observability()


app = FastAPI(
    title="HelpmateAI API",
    version="0.1.0",
    description="Thin FastAPI boundary over the existing HelpmateAI RAG core.",
    lifespan=_lifespan,
)

cors_origins = list(settings.cors_origins)
allow_all_origins = "*" in cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if allow_all_origins else cors_origins,
    allow_credentials=not allow_all_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount the Lemon Squeezy billing routes (POST /webhooks/lemonsqueezy +
# POST /billing/portal). Defined in backend/billing_routes.py so the
# webhook signature verification + customer-portal API helpers stay
# out of this 600-line main module. Both routes degrade gracefully
# (503) when HELPMATE_LEMONSQUEEZY_* env vars are unset, so this
# mount is safe in environments without LS configured.
app.include_router(billing_router)


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
def _feedback_store() -> FeedbackStore:
    # Backend mirrors HELPMATE_STATE_STORE_BACKEND: local JSON file at
    # data/api_state/feedback.json for dev, Supabase's helpmate_feedback
    # table (with RLS) for production. See backend/feedback_store.py.
    return build_feedback_store(_settings())


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


@app.get("/health/sentry-debug")
def sentry_debug() -> None:
    """Raise an unhandled exception so Sentry sees the issue end-to-end.

    Used once at deploy time to confirm the DSN, environment, and
    release tag are wired. The route returns no JSON — Sentry's
    FastAPI integration catches the raise, ships the event, and
    FastAPI's default 500 handler returns "Internal Server Error" to
    the caller. There is intentionally no auth on this route: it must
    be callable from anywhere with curl. Remove or gate it behind a
    feature flag if your threat model objects.
    """
    # ``division by zero`` is the canonical Sentry-tutorial sample;
    # keeping it deliberately recognizable so anyone reading the issue
    # title in Sentry knows it's a smoke test, not a real bug.
    division_by_zero = 1 / 0  # noqa: F841 — intentional crash for Sentry verification
    return None


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
        retention_days=limits["retention_days"],
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

    # PostHog event — server-side capture so the analytics dashboard
    # sees the same /qa volume the user actually pays for, including
    # API-only callers that never hit the browser. The event name
    # mirrors the FE's "asked_question" convention so funnels join
    # cleanly. No question text in properties (PII).
    answer_dict = answer.to_dict()
    trace_id_for_event = answer_dict.get("run_trace_id") or answer_dict.get("trace_id")
    capture_event(
        distinct_id=user.id,
        event="qa_answered",
        properties={
            "tier": tier,
            "premium": bool(payload.premium),
            "model": active_model,
            "document_id": payload.document_id,
            "trace_id": trace_id_for_event,
            "support_status": answer_dict.get("support_status"),
        },
    )

    # PostHog LLM Analytics — fan out one ``$ai_generation`` event per
    # underlying LLM call so the LLM Analytics dashboard sees full
    # token / cost / model attribution per span. Trace_id ties the
    # spans together so the dashboard can group them per request.
    # ``answer.llm_breakdown`` stays None on cache hits (so a cached
    # answer doesn't replay events that never happened on this
    # request) — bail in that case. The contract:
    #   PostHog LLM Analytics expects ``$ai_*`` property keys verbatim;
    #   missing them silently drops the event off the dashboard.
    llm_breakdown = getattr(answer, "llm_breakdown", None)
    if llm_breakdown:
        for call in llm_breakdown.get("calls", []) or []:
            try:
                capture_event(
                    distinct_id=user.id,
                    event="$ai_generation",
                    properties={
                        "$ai_trace_id": trace_id_for_event,
                        "$ai_span_name": call.get("task_name") or "llm_call",
                        "$ai_provider": "openai",
                        "$ai_model": call.get("model_name") or "",
                        "$ai_input_tokens": int(call.get("prompt_tokens") or 0),
                        "$ai_output_tokens": int(call.get("completion_tokens") or 0),
                        "$ai_total_cost_usd": float(call.get("cost_usd") or 0.0),
                        "$ai_is_error": bool(call.get("error")),
                        "$ai_error": call.get("error"),
                        # Non-$ai_ properties carry app context for
                        # funnel building alongside the LLM Analytics
                        # dashboard. PostHog ignores unknown $ai_*
                        # fields but happily indexes plain ones.
                        "tier": tier,
                        "document_id": payload.document_id,
                        "premium": bool(payload.premium),
                        "raw_response_id": call.get("raw_response_id"),
                        "response_format": call.get("response_format"),
                    },
                )
            except Exception:
                # Per-call $ai_generation is best-effort; a partial
                # failure mustn't tank the /qa response. The qa_answered
                # event above is the canonical funnel signal.
                continue
    return AskResponse(answer=answer_dict)


@app.post("/feedback", response_model=FeedbackResponse)
def submit_feedback(
    payload: FeedbackRequest,
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> FeedbackResponse:
    """Record a thumb-up / thumb-down (and optional comment) against
    an answer or any surface that produced a trace_id.

    Item 2 of the UX Pack. The frontend renders the buttons under
    each answer card and POSTs here on click; the row lands in
    ``helpmate_feedback`` keyed on (user_id, trace_id) so dashboards
    can join with ``helpmate_run_traces`` for the model × cost × rating
    rollup.

    Auth required. RLS scopes inserts to the calling user — the table
    enforces ``auth.uid() = user_id`` with check, and the service-side
    code also passes the authenticated user_id explicitly so a bad
    payload can't write under another account even with service-role
    bypass.

    Validation:
      • rating ∈ {'up', 'down'} (else 400)
      • comment ≤ 2000 chars (else 400)
      • trace_id is opaque — we don't pre-verify it points at a real
        trace row. Stale / unknown trace_ids land in the table for
        analytics consistency rather than failing the user's tap.
    """
    comment = (payload.comment or "").strip()
    surface = (payload.surface or "answer").strip() or "answer"
    trace_id = (payload.trace_id or "").strip() or None

    try:
        record = _feedback_store().save_feedback(
            user_id=user.id,
            rating=payload.rating,
            trace_id=trace_id,
            surface=surface,
            comment=comment,
        )
    except FeedbackValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        # Storage failure (Supabase outage, disk full on local) — we
        # surface a 503 so the frontend can retry once. Feedback is
        # the user's gesture; a silent drop would feel buggy.
        logger.warning("Feedback save failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Feedback couldn't be saved. Please try again.",
        ) from exc

    capture_event(
        distinct_id=user.id,
        event="feedback_submitted",
        properties={
            "rating": payload.rating,
            "surface": surface,
            "trace_id": trace_id,
            "has_comment": bool(comment),
        },
    )

    return FeedbackResponse(
        feedback_id=record.feedback_id,
        created_at=record.created_at,
    )


@app.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_audio(
    file: UploadFile = File(...),
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> TranscribeResponse:
    """Transcribe a short audio clip via OpenAI Whisper.

    Powers the "speak your question" affordance on the workspace. The
    frontend records a single utterance with the MediaRecorder API and
    POSTs the blob here; we hand it off to Whisper and return the
    transcript so the textarea can populate without the user typing a
    multi-paragraph question.

    Free for every tier — no quota gate. The cost per call is tiny
    (Whisper bills $0.006/min) and the feature is a UX win, not a
    paid-tier hook. We still require auth so the route can't be
    abused as an anonymous transcription API.

    Validates upload type + size BEFORE touching OpenAI so a misfiled
    blob (or a 1 GB stream) gets a fast 400/413 instead of burning
    minutes of audio at the API edge.
    """
    settings = _settings()
    if not settings.openai_api_key:
        # The frontend gates the voice button on whether transcription
        # is configured; if a request still lands here without a key,
        # surface a clear 503 rather than crashing on a None client.
        raise HTTPException(
            status_code=503,
            detail="Voice transcription is not configured for this deployment.",
        )

    content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    if content_type and content_type not in SUPPORTED_TRANSCRIBE_AUDIO_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio content-type: {content_type}.",
        )

    # Stream the upload in 64 KB chunks so an oversized blob fails
    # fast at 413 BEFORE we hold the whole payload in memory. Reading
    # the full body first (the original implementation) lets a hostile
    # 1 GB upload allocate ~1 GB of RSS on this process before the
    # size check runs — Vercel's container would OOM long before
    # returning the 413. The 64 KB chunk size is a round number large
    # enough to keep the read syscall count reasonable while small
    # enough that the early-bail check fires on the first chunk past
    # the cap. Codex flagged this as P1 on PR #5.
    CHUNK_SIZE = 64 * 1024
    audio_chunks: list[bytes] = []
    audio_size = 0
    while True:
        chunk = await file.read(CHUNK_SIZE)
        if not chunk:
            break
        audio_size += len(chunk)
        if audio_size > TRANSCRIBE_MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Audio recording exceeds the 25 MB Whisper limit.",
            )
        audio_chunks.append(chunk)
    if not audio_chunks:
        raise HTTPException(
            status_code=400,
            detail="Audio payload is empty.",
        )
    audio_bytes = b"".join(audio_chunks)

    # Late import so the test suite + harness paths that never hit the
    # route don't pay the openai SDK import cost on every cold start.
    # The downstream call mirrors the OpenAIService construction pattern
    # used elsewhere — a missing key surfaces as the 503 above.
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("OpenAI SDK import failed for /transcribe: %s", exc)
        raise HTTPException(status_code=503, detail="Voice transcription unavailable.") from exc

    suffix = Path(file.filename or "audio.webm").suffix.lower() or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as buffer:
        buffer.write(audio_bytes)
        tmp_path = Path(buffer.name)

    started_at = datetime.now(timezone.utc)

    # Two-step transcription chain: ``whisper-1`` is the default
    # (cheaper, ubiquitous, supports verbose_json with duration);
    # ``gpt-4o-mini-transcribe`` is the fallback for cases where
    # the Whisper endpoint itself is unavailable. The fallback only
    # fires on a Whisper-side exception — payload-level rejections
    # (mime/size/empty) already short-circuited earlier with their
    # own 400/413 responses. CodeRabbit flagged on PR #5 that the
    # PR description advertised a fallback but the code only had
    # Whisper.
    def _call_transcribe_sync(model_name: str) -> object:
        """Blocking OpenAI transcription call — the SDK is sync-only
        for audio.transcriptions as of v1.98 (blocking POST +
        multipart upload via ``requests``). The caller must hand this
        to ``asyncio.to_thread`` so the FastAPI event loop stays
        responsive (Codex P1).

        ``verbose_json`` carries the ``duration`` field for whisper-1;
        gpt-4o-mini-transcribe returns the standard ``json`` shape
        without ``duration`` — we'll fall back to wall-clock in that
        case (covered by the post-call duration resolution below).
        """
        # Cheap rebuild — no auth round-trip — so we can construct
        # per attempt; keeps state local to the call.
        client = OpenAI(api_key=settings.openai_api_key)
        with tmp_path.open("rb") as audio_handle:
            response_format = "verbose_json" if model_name == "whisper-1" else "json"
            return client.audio.transcriptions.create(
                model=model_name,
                file=audio_handle,
                response_format=response_format,
            )

    response = None
    primary_error: Exception | None = None
    try:
        try:
            response = await asyncio.to_thread(_call_transcribe_sync, "whisper-1")
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning(
                "Whisper transcription failed; trying gpt-4o-mini-transcribe fallback (%s)",
                exc.__class__.__name__,
            )
            primary_error = exc
            try:
                response = await asyncio.to_thread(
                    _call_transcribe_sync, "gpt-4o-mini-transcribe"
                )
            except HTTPException:
                raise
            except Exception as fallback_exc:
                logger.warning(
                    "Fallback gpt-4o-mini-transcribe also failed (%s); surfacing 502",
                    fallback_exc.__class__.__name__,
                )
                # Surface the FALLBACK error (more recent + actionable
                # signal for ops). Chain both via ``__context__`` /
                # ``from`` so the traceback shows the original Whisper
                # failure too.
                raise HTTPException(
                    status_code=502,
                    detail="Transcription service is temporarily unavailable.",
                ) from fallback_exc
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

    transcript = str(getattr(response, "text", "") or "").strip()
    duration = getattr(response, "duration", None)
    if duration is None and isinstance(response, dict):
        duration = response.get("duration")
    try:
        duration_value = float(duration) if duration is not None else 0.0
    except (TypeError, ValueError):
        duration_value = 0.0
    if duration_value <= 0:
        elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
        duration_value = max(elapsed, 0.0)
    return TranscribeResponse(text=transcript, duration_seconds=round(duration_value, 3))


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
