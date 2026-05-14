"""Monthly quota counters — local + Supabase backends.

Step 3 of the tier-enforcement series. Defines the QuotaStore protocol
that the /qa handler reads/writes through, plus two implementations:

  LocalQuotaStore     JSON file at <data_dir>/api_state/quota_counters.json.
                      Single-process, no locking. Good enough for dev and
                      tests; not safe under concurrent workers.
  SupabaseQuotaStore  Calls the increment_question_counter /
                      increment_premium_counter RPC functions from
                      docs/supabase-quota-counters.sql. Atomic upsert
                      under concurrent /qa requests.

The factory `build_quota_store(settings)` mirrors the existing
`build_api_record_store` pattern — picks the backend based on
`settings.uses_supabase_state`.

Shape returned to callers:

    QuotaCounter(questions: int, premium: int)

A counter that doesn't exist yet (no row for the current month) is
returned as `QuotaCounter(0, 0)` — callers should never need to
distinguish "no row" from "row with zeroes."

Increment methods (`increment_questions`, `increment_premium`) return
the NEW value after the atomic upsert so the route can compare it
against the tier cap without a separate read. The Local backend
simulates the same semantics with a read-modify-write.
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from src.cloud import create_supabase_client
from src.config import Settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QuotaCounter:
    """Per-user-per-month counter snapshot."""

    questions: int = 0
    premium: int = 0


def current_period_start(now: datetime | None = None) -> date:
    """Return the first day of the current UTC month as a date.

    Matches the schema's `period_start date` column and the SQL
    expression `date_trunc('month', timezone('utc', now()))::date`. The
    `now` parameter is for tests — defaults to "real" UTC now.
    """
    moment = now or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).date().replace(day=1)


class QuotaStore(ABC):
    """Read + atomic-increment interface for per-month quota counters.

    Implementations must guarantee that `increment_*` is atomic: two
    concurrent calls for the same user must produce distinct return
    values (N+1 and N+2), never both returning N+1.
    """

    @abstractmethod
    def get_counter(self, user_id: str) -> QuotaCounter:
        """Snapshot of the user's counter for the current month."""

    @abstractmethod
    def increment_questions(self, user_id: str) -> int:
        """Atomically add 1 to questions, return the new total."""

    @abstractmethod
    def increment_premium(self, user_id: str) -> int:
        """Atomically add 1 to premium, return the new total."""


class LocalQuotaStore(QuotaStore):
    """JSON-file-backed counter store for local dev + tests.

    File layout:
        {
          "<user_id>": {
            "<period_start_iso>": {"questions": int, "premium": int}
          }
        }

    Single-process, no concurrency guarantees beyond what the file
    system provides. Read-modify-write is acceptable here because
    production runs the Supabase backend; the local backend exists
    so the gate works in dev without a database.
    """

    def __init__(self, settings: Settings) -> None:
        root = settings.data_dir / "api_state"
        root.mkdir(parents=True, exist_ok=True)
        self._path = root / "quota_counters.json"

    # ---- file I/O -------------------------------------------------------

    def _read(self) -> dict[str, dict[str, dict[str, int]]]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # Corrupt file means we lose this month's history — log and
            # start fresh rather than crash the route. Production runs
            # the Supabase backend, so this only affects dev.
            logger.warning("LocalQuotaStore: failed to read %s: %s", self._path, exc)
            return {}

    def _write(self, data: dict[str, dict[str, dict[str, int]]]) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    # ---- QuotaStore -----------------------------------------------------

    def get_counter(self, user_id: str) -> QuotaCounter:
        data = self._read()
        period_key = current_period_start().isoformat()
        entry = data.get(user_id, {}).get(period_key, {})
        return QuotaCounter(
            questions=int(entry.get("questions", 0)),
            premium=int(entry.get("premium", 0)),
        )

    def _bump(self, user_id: str, field: str) -> int:
        data = self._read()
        period_key = current_period_start().isoformat()
        user_block = data.setdefault(user_id, {})
        period_block = user_block.setdefault(period_key, {"questions": 0, "premium": 0})
        period_block[field] = int(period_block.get(field, 0)) + 1
        self._write(data)
        return int(period_block[field])

    def increment_questions(self, user_id: str) -> int:
        return self._bump(user_id, "questions")

    def increment_premium(self, user_id: str) -> int:
        return self._bump(user_id, "premium")


class SupabaseQuotaStore(QuotaStore):
    """Supabase-backed quota store using the atomic RPC functions.

    Reads go through the table directly (no atomicity needed). Writes
    go through the `increment_question_counter` / `increment_premium_counter`
    RPCs which use ON CONFLICT ... DO UPDATE ... RETURNING for atomicity.
    """

    def __init__(self, settings: Settings) -> None:
        self.client = create_supabase_client(settings.supabase_url, settings.supabase_key)
        self.table_name = settings.supabase_quota_counters_table

    def get_counter(self, user_id: str) -> QuotaCounter:
        period_key = current_period_start().isoformat()
        result = (
            self.client.table(self.table_name)
            .select("questions, premium")
            .eq("user_id", user_id)
            .eq("period_start", period_key)
            .limit(1)
            .execute()
        )
        rows = getattr(result, "data", None) or []
        if not rows:
            return QuotaCounter()
        row = rows[0]
        return QuotaCounter(
            questions=int(row.get("questions") or 0),
            premium=int(row.get("premium") or 0),
        )

    def _rpc(self, fn_name: str, user_id: str) -> int:
        # The RPC returns a bare integer. supabase-py wraps it in a list
        # with one element when called via .rpc — `result.data` is the
        # int (newer client) or [int] (older). Handle both.
        result = self.client.rpc(fn_name, {"p_user_id": user_id}).execute()
        data = getattr(result, "data", None)
        if isinstance(data, list):
            return int(data[0]) if data else 0
        if data is None:
            return 0
        return int(data)

    def increment_questions(self, user_id: str) -> int:
        return self._rpc("increment_question_counter", user_id)

    def increment_premium(self, user_id: str) -> int:
        return self._rpc("increment_premium_counter", user_id)


def build_quota_store(settings: Settings) -> QuotaStore:
    """Pick the right backend based on settings.uses_supabase_state."""
    if settings.uses_supabase_state:
        return SupabaseQuotaStore(settings)
    return LocalQuotaStore(settings)
