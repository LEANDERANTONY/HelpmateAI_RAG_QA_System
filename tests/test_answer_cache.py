import shutil
from pathlib import Path

from src.cache.answer_cache import AnswerCache
from src.schemas import AnswerResult, CacheStatus


def test_answer_cache_round_trip():
    cache_dir = Path("data") / "test-answer-cache"
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    cache = AnswerCache(cache_dir)
    key = cache.build_key("fingerprint", "What is covered?", "v1", "v1", "gpt")
    answer = AnswerResult(
        question="What is covered?",
        answer="Coverage is limited to the cited clauses.",
        citations=["Page 2"],
        evidence=[],
        supported=True,
        cache_status=CacheStatus(index_reused=False, answer_cache_hit=False),
        model_name="gpt",
    )

    cache.set(key, answer)
    cached = cache.get(key)

    assert cached is not None
    assert cached.answer == answer.answer
    assert cached.supported is True
    assert cached.cache_status.answer_cache_hit is True

    shutil.rmtree(cache_dir)
