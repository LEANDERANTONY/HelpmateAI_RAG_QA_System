"""Tests for backend.store's bounded retry-with-backoff around Supabase reads.

These prove that a transient infra blip (DNS/connection glitch, or the narrow
clock-skew JWT glitch) no longer bubbles out of a store read and crashes an
idempotent cron run, while a genuine error still fails fast without spinning.

Regression coverage for HELPMATE-BACKEND-C ("JWT issued at future") and
HELPMATE-BACKEND-D (transient DNS ConnectError).
"""
from __future__ import annotations

import httpx
import pytest

from backend import store as store_module
from backend.store import SupabaseApiRecordStore, _execute_with_retry


class _FlakyQuery:
    """A query whose .execute() raises `error` on the first N calls, then
    returns `result`. Records how many times execute() was invoked."""

    def __init__(self, error: Exception, result, *, fail_times: int = 1):
        self._error = error
        self._result = result
        self._fail_times = fail_times
        self.calls = 0

    def execute(self):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise self._error
        return self._result


class _AlwaysFails:
    def __init__(self, error: Exception):
        self._error = error
        self.calls = 0

    def execute(self):
        self.calls += 1
        raise self._error


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Keep the backoff instantaneous so the suite stays fast."""
    monkeypatch.setattr(store_module.time, "sleep", lambda _s: None)


def _connect_error() -> httpx.ConnectError:
    return httpx.ConnectError("[Errno -5] No address associated with hostname")


# --- _execute_with_retry ---------------------------------------------------


def test_retries_transient_connect_error_then_succeeds():
    query = _FlakyQuery(_connect_error(), {"data": [{"payload": {"ok": True}}]})
    response = _execute_with_retry(query)
    assert response == {"data": [{"payload": {"ok": True}}]}
    assert query.calls == 2  # failed once, retried, succeeded


def test_non_transient_error_fails_fast_without_retry():
    query = _AlwaysFails(ValueError("real bug, not a blip"))
    with pytest.raises(ValueError):
        _execute_with_retry(query)
    assert query.calls == 1  # no retry on a real error


def test_transient_error_reraised_after_exhausting_attempts():
    query = _AlwaysFails(_connect_error())
    with pytest.raises(httpx.ConnectError):
        _execute_with_retry(query, attempts=3)
    assert query.calls == 3  # bounded: exactly `attempts` tries, then re-raise


def test_transient_jwt_clock_skew_is_retried():
    api_error = store_module.APIError({"message": "JWT issued at future"})
    query = _FlakyQuery(api_error, {"data": []})
    response = _execute_with_retry(query)
    assert response == {"data": []}
    assert query.calls == 2


def test_real_auth_apierror_fails_fast():
    api_error = store_module.APIError({"message": "invalid JWT: token is expired"})
    query = _AlwaysFails(api_error)
    with pytest.raises(store_module.APIError):
        _execute_with_retry(query)
    assert query.calls == 1  # a real auth error is not masked by retries


# --- wired into the store read methods -------------------------------------


class _FakeTableQuery(_FlakyQuery):
    """Fake query that also supports the fluent .select()/.eq() chain the
    store builds before handing the query to _execute_with_retry."""

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self


class _FakeClient:
    def __init__(self, query):
        self._query = query

    def table(self, _name):
        return self._query


def _store_with_query(query) -> SupabaseApiRecordStore:
    store = SupabaseApiRecordStore.__new__(SupabaseApiRecordStore)
    store.client = _FakeClient(query)
    store.documents_table = "helpmate_documents"
    store.indexes_table = "helpmate_indexes"
    return store


def test_list_documents_returns_rows_after_transient_blip():
    payload = {
        "document_id": "d1",
        "file_name": "a.pdf",
        "file_type": "pdf",
        "source_path": "/tmp/a.pdf",
        "fingerprint": "f1",
        "char_count": 10,
        "page_count": 1,
    }
    query = _FakeTableQuery(_connect_error(), {"data": [{"payload": payload}]})
    store = _store_with_query(query)
    documents = store.list_documents()
    assert query.calls == 2  # retried past the blip
    assert [d.document_id for d in documents] == ["d1"]


def test_list_indexes_fails_fast_on_real_error():
    query = _FakeTableQuery(RuntimeError("schema mismatch"), {"data": []})
    query._fail_times = 99  # always fails
    store = _store_with_query(query)
    with pytest.raises(RuntimeError):
        store.list_indexes()
    assert query.calls == 1  # no retry on a non-transient error
