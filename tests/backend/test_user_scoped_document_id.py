"""Per-user document_id (H1) and per-user answer-cache key (M22).

H1: a content-only document_id let one user overwrite another's workspace row
(same bytes -> same id -> upsert clobber). The id is now derived from
owner_id + fingerprint, while the raw content fingerprint stays separate for
index/cache reuse. Owner-less (legacy) callers keep the content-only id, so the
change is non-destructive.

M22: the answer-cache key now incorporates the (per-user) document_id, so a
premium answer paid for by one user is never served to another with identical
content.
"""
from __future__ import annotations

from src.cache.answer_cache import AnswerCache
from src.ingest.service import derive_document_id


_BASE_KEY = dict(
    fingerprint="ff",
    question="What is covered?",
    retrieval_version="v1",
    generation_version="v1",
    model_name="gpt-5.4-mini",
)


def test_derive_document_id_is_per_user():
    fingerprint = "a" * 64
    a = derive_document_id(fingerprint, "user-a")
    b = derive_document_id(fingerprint, "user-b")
    assert a != b
    assert len(a) == 16 and len(b) == 16


def test_derive_document_id_legacy_is_content_only():
    fingerprint = "a" * 64
    assert derive_document_id(fingerprint) == fingerprint[:16]
    assert derive_document_id(fingerprint, None) == fingerprint[:16]
    assert derive_document_id(fingerprint, "") == fingerprint[:16]


def test_cache_key_scoped_by_document_id():
    key_a = AnswerCache.build_key(**_BASE_KEY, document_id="doc-user-a")
    key_b = AnswerCache.build_key(**_BASE_KEY, document_id="doc-user-b")
    assert key_a != key_b


def test_cache_key_without_document_id_is_unchanged():
    # Legacy callers (no document_id) keep the original key — non-breaking.
    assert AnswerCache.build_key(**_BASE_KEY) == AnswerCache.build_key(**_BASE_KEY, document_id="")
