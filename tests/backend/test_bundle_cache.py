"""Per-request artifact-bundle memoization (H2).

One retrieve() reloads the full artifact bundle 5-8x (load_chunks /
load_sections / load_synopses / load_topology_edges + every dense_query's
load_index_record). When the pipeline binds a request-scoped cache, those
collapse to a single fetch; unbound (indexing, eval) every call still goes to
the store unchanged.
"""
from __future__ import annotations

from src.retrieval.store import ChromaIndexStore, bind_bundle_cache, reset_bundle_cache


class _CountingArtifactStore:
    def __init__(self):
        self.calls = 0

    def load_bundle(self, fingerprint):
        self.calls += 1
        return {"index_record": {}, "chunks": [], "sections": [], "synopses": [], "topology_edges": []}


def _store_with(fake) -> ChromaIndexStore:
    # Bypass the heavy ChromaIndexStore.__init__ — we only exercise the cached
    # bundle accessor, which depends solely on self.artifact_store.
    store = object.__new__(ChromaIndexStore)
    store.artifact_store = fake
    return store


def test_bundle_fetched_once_within_bound_request():
    fake = _CountingArtifactStore()
    store = _store_with(fake)
    token = bind_bundle_cache()
    try:
        first = store._load_bundle_cached("fp")
        second = store._load_bundle_cached("fp")
    finally:
        reset_bundle_cache(token)
    assert first is second
    assert fake.calls == 1


def test_bundle_not_cached_without_binding():
    fake = _CountingArtifactStore()
    store = _store_with(fake)
    store._load_bundle_cached("fp")
    store._load_bundle_cached("fp")
    assert fake.calls == 2
