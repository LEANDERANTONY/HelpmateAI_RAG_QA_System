"""Online feedback storage — local + Supabase backends.

Item 2 of the UX & Iteration Pack. Captures thumb-up / thumb-down
ratings (with an optional free-text comment) joined to
``helpmate_run_traces.trace_id`` so dashboards can correlate user
satisfaction with the model + cost columns that the Production Safety
Pack already landed.

Mirrors the existing two-backend pattern:

  LocalFeedbackStore     JSON file at <data_dir>/api_state/feedback.json.
                         Single-process, no locking. Good enough for
                         dev and tests; not safe under concurrent
                         workers.
  SupabaseFeedbackStore  Inserts into ``public.helpmate_feedback``
                         (see docs/supabase-feedback.sql). RLS is
                         service-role-bypass on write — the FastAPI
                         service inserts user-bound rows directly.

The factory `build_feedback_store(settings)` follows the same pattern
as `build_quota_store` — picks the backend based on
`settings.uses_supabase_state`.

Shape returned to callers:

    FeedbackRecord(feedback_id, user_id, trace_id, surface, rating,
                   comment, created_at)

`save_feedback` returns the persisted record (server-generated
``feedback_id`` + ``created_at`` echoed back to the client) so the
optimistic UI on the frontend can swap in the real row id once the
POST resolves.
"""
from __future__ import annotations

import json
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.cloud import create_supabase_client
from src.config import Settings


logger = logging.getLogger(__name__)


_VALID_RATINGS = ("up", "down")
_DEFAULT_SURFACE = "answer"
# Hard cap on the free-text comment field. The frontend's textarea
# soft-caps the same value; this is the defensive backend gate so a
# misbehaving client can't push 1 MB of text into the column.
COMMENT_MAX_LENGTH = 2000


@dataclass(frozen=True)
class FeedbackRecord:
    """One persisted feedback row, in API shape (ISO timestamps).

    Returned by save_feedback and serialized straight into the route's
    response. ``trace_id`` is optional — some surfaces (a copy button,
    a citation pill) may produce feedback events outside a trace
    lifecycle, so we don't enforce a FK at the DB layer or here.
    """

    feedback_id: str
    user_id: str
    trace_id: str | None
    surface: str
    rating: str
    comment: str
    created_at: str

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


class FeedbackValidationError(ValueError):
    """Raised when the caller-supplied feedback payload fails the
    shared validation rules (rating not in {'up','down'}, comment too
    long, etc.). The route translates this to a 400.

    Centralizing validation here means the LocalFeedbackStore + the
    Supabase one apply the same rules. Without this both code paths
    would have to duplicate the rating-enum check (which is also
    enforced at the DB layer for the Supabase backend, but the local
    backend has no equivalent — so the explicit check is what keeps
    the two backends behaving identically)."""


def _validate_payload(*, rating: str, comment: str, surface: str) -> None:
    if rating not in _VALID_RATINGS:
        raise FeedbackValidationError(
            f"rating must be one of {_VALID_RATINGS}, got {rating!r}"
        )
    if len(comment) > COMMENT_MAX_LENGTH:
        raise FeedbackValidationError(
            f"comment exceeds {COMMENT_MAX_LENGTH} character limit"
        )
    if not surface or not isinstance(surface, str):
        raise FeedbackValidationError("surface must be a non-empty string")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class FeedbackStore(ABC):
    """Append-only persistence interface for feedback rows.

    Implementations validate the payload, generate a uuid for the row,
    stamp ``created_at``, and persist. Returns the persisted record.
    """

    @abstractmethod
    def save_feedback(
        self,
        *,
        user_id: str,
        rating: str,
        trace_id: str | None = None,
        surface: str = _DEFAULT_SURFACE,
        comment: str = "",
    ) -> FeedbackRecord:
        ...

    @abstractmethod
    def list_for_user(self, user_id: str, *, limit: int = 50) -> list[FeedbackRecord]:
        """Most-recent-first slice of a user's feedback history. Used by
        the per-user history view (deferred — admins read the table
        directly today)."""


class LocalFeedbackStore(FeedbackStore):
    """JSON-file-backed feedback store for local dev + tests.

    File layout:
        [
          {"feedback_id": "...", "user_id": "...", ...},
          ...
        ]
    Append-only — no in-place updates. Single-process, no concurrency
    guarantees beyond what the file system provides; production uses
    the Supabase backend, so this is purely for dev parity.
    """

    def __init__(self, settings: Settings) -> None:
        root = settings.data_dir / "api_state"
        root.mkdir(parents=True, exist_ok=True)
        self._path = root / "feedback.json"

    def _read(self) -> list[dict[str, str | None]]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("LocalFeedbackStore: read failed (%s); starting fresh", exc)
            return []
        if not isinstance(data, list):
            logger.warning("LocalFeedbackStore: corrupt root (expected list); starting fresh")
            return []
        return data

    def _write(self, rows: list[dict[str, str | None]]) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def save_feedback(
        self,
        *,
        user_id: str,
        rating: str,
        trace_id: str | None = None,
        surface: str = _DEFAULT_SURFACE,
        comment: str = "",
    ) -> FeedbackRecord:
        _validate_payload(rating=rating, comment=comment, surface=surface)
        record = FeedbackRecord(
            feedback_id=str(uuid.uuid4()),
            user_id=user_id,
            trace_id=trace_id,
            surface=surface,
            rating=rating,
            comment=comment,
            created_at=_now_iso(),
        )
        rows = self._read()
        rows.append(record.to_dict())
        self._write(rows)
        return record

    def list_for_user(self, user_id: str, *, limit: int = 50) -> list[FeedbackRecord]:
        rows = [row for row in self._read() if row.get("user_id") == user_id]
        rows.sort(key=lambda row: row.get("created_at") or "", reverse=True)
        return [FeedbackRecord(**row) for row in rows[:limit]]


class SupabaseFeedbackStore(FeedbackStore):
    """Supabase-backed feedback store.

    Inserts into ``public.helpmate_feedback``. The DB enforces the
    rating CHECK and the auth.users FK; we still call
    ``_validate_payload`` first so a bad request returns a clean 400
    instead of cascading into a Supabase error.
    """

    def __init__(self, settings: Settings) -> None:
        self.client = create_supabase_client(settings.supabase_url, settings.supabase_key)
        # No env var override yet — the column / table names are
        # stable. If a future migration renames the table, surface
        # this as a Settings field like the existing supabase_*_table
        # entries.
        self.table_name = "helpmate_feedback"

    def save_feedback(
        self,
        *,
        user_id: str,
        rating: str,
        trace_id: str | None = None,
        surface: str = _DEFAULT_SURFACE,
        comment: str = "",
    ) -> FeedbackRecord:
        _validate_payload(rating=rating, comment=comment, surface=surface)
        # Pre-generate the uuid client-side so we can echo it back to
        # the caller WITHOUT a follow-up SELECT round-trip. Mirrors the
        # quota_store pattern of "atomic op returns the persisted
        # state."
        feedback_id = str(uuid.uuid4())
        created_at = _now_iso()
        payload = {
            "feedback_id": feedback_id,
            "user_id": user_id,
            "trace_id": trace_id,
            "surface": surface,
            "rating": rating,
            "comment": comment,
            "created_at": created_at,
        }
        try:
            self.client.table(self.table_name).insert(payload).execute()
        except Exception as exc:
            # Bubble up — the route turns this into a 500 with a
            # logged warning. Unlike the run-traces store (which
            # warns + drops the row to avoid breaking /qa for
            # observability reasons), feedback IS the request the
            # user made, so a silent drop would feel broken.
            logger.warning("SupabaseFeedbackStore.save failed: %s", exc)
            raise
        return FeedbackRecord(**payload)

    def list_for_user(self, user_id: str, *, limit: int = 50) -> list[FeedbackRecord]:
        try:
            result = (
                self.client.table(self.table_name)
                .select("feedback_id, user_id, trace_id, surface, rating, comment, created_at")
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
        except Exception as exc:
            logger.warning("SupabaseFeedbackStore.list failed: %s", exc)
            return []
        rows = getattr(result, "data", None) or []
        return [FeedbackRecord(**row) for row in rows]


def build_feedback_store(settings: Settings) -> FeedbackStore:
    """Pick the right backend based on settings.uses_supabase_state.

    Mirrors build_quota_store — the local backend exists so the
    feature works in dev without a database, while production runs
    on Supabase with RLS-backed user isolation.
    """
    if settings.uses_supabase_state:
        return SupabaseFeedbackStore(settings)
    return LocalFeedbackStore(settings)
