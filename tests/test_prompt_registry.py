"""Tests for the prompt-registry loader.

Covers loader behavior + the shape contract for every prompt JSON
shipped in this commit. The shape contract test is the safety net for
prompt migrations: if a v1 file gets edited in a way that drops the
``user`` field, or the registry.json points at a missing version, the
test fails before the route ships the half-loaded prompt to OpenAI.

Loader paths:
  • get_prompt(name) resolves the active version from registry.json
  • get_prompt(name, version="v1") loads an explicit version
  • Missing template raises PromptNotFoundError (clean, not generic)
  • Missing placeholder in render raises PromptRenderError
  • Mtime invalidation: editing a template picks up the change
  • Cache hits don't re-parse the file (mtime unchanged)
  • Placeholder discovery: each shipped template exposes its keys

Shape contract:
  • Every active prompt in registry.json has a JSON file at the path
  • Every JSON file parses + has name/version/system/user fields
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from backend import prompt_registry
from backend.prompt_registry import (
    PROMPTS_ROOT,
    PromptNotFoundError,
    PromptRegistryError,
    PromptRenderError,
    PromptTemplate,
    clear_cache,
    get_prompt,
    list_active_versions,
    render_prompt,
)


@pytest.fixture(autouse=True)
def _reset_registry_cache():
    """Drop the module-level cache between tests so a test that points
    the loader at a tmp dir doesn't leak templates into the next
    test."""
    clear_cache()
    yield
    clear_cache()


# ─── loader behavior ────────────────────────────────────────────────────


def test_get_prompt_resolves_active_version_from_registry():
    template = get_prompt("answer_generation")
    assert template.name == "answer_generation"
    assert template.version == "v1"
    assert template.system
    assert template.user


def test_get_prompt_with_explicit_version():
    template = get_prompt("answer_generation", "v1")
    assert template.version == "v1"


def test_get_prompt_missing_active_version_raises_clean_error(tmp_path):
    # Empty registry + no explicit version → PromptNotFoundError, not
    # a generic KeyError.
    prompts_root = tmp_path / "prompts"
    prompts_root.mkdir()
    (prompts_root / "registry.json").write_text(
        json.dumps({"active": {}}), encoding="utf-8"
    )
    with pytest.raises(PromptNotFoundError):
        get_prompt("anything", prompts_root=prompts_root)


def test_get_prompt_missing_file_raises_clean_error(tmp_path):
    prompts_root = tmp_path / "prompts"
    prompts_root.mkdir()
    (prompts_root / "registry.json").write_text(
        json.dumps({"active": {"foo": "v1"}}), encoding="utf-8"
    )
    with pytest.raises(PromptNotFoundError):
        get_prompt("foo", prompts_root=prompts_root)


def test_render_missing_placeholder_raises_render_error():
    # answer_generation expects {{question}}, {{evidence}},
    # {{summary_instructions}} — omit one and watch the loader
    # complain.
    template = get_prompt("answer_generation")
    with pytest.raises(PromptRenderError) as excinfo:
        template.render(question="q", summary_instructions="")  # evidence missing
    assert "evidence" in str(excinfo.value)


def test_render_substitutes_placeholders():
    template = get_prompt("answer_generation")
    rendered = template.render(
        question="What does Article 5 say?",
        evidence="[Source 1 | p.5]\nArticle 5: parties shall...",
        summary_instructions="",
    )
    assert "What does Article 5 say?" in rendered["user"]
    assert "[Source 1 | p.5]" in rendered["user"]
    # Placeholder markers must be fully consumed.
    assert "{{question}}" not in rendered["user"]
    assert "{{evidence}}" not in rendered["user"]
    assert "{{summary_instructions}}" not in rendered["user"]


def test_placeholders_reports_keys():
    template = get_prompt("answer_generation")
    assert template.placeholders() == {"question", "evidence", "summary_instructions"}


def test_render_prompt_convenience_returns_messages():
    rendered = render_prompt(
        "support_verifier",
        question="q?",
        answer="a.",
        evidence="[Source 1] e.",
    )
    assert rendered["system"]
    assert "[Source 1]" in rendered["user"]


# ─── caching + mtime invalidation ────────────────────────────────────────


def test_cache_hit_returns_same_instance(tmp_path):
    """Two calls for the same template should return the same
    PromptTemplate instance — proves the cache is alive."""
    prompts_root = tmp_path / "prompts"
    prompts_root.mkdir()
    (prompts_root / "registry.json").write_text(
        json.dumps({"active": {"test": "v1"}}), encoding="utf-8"
    )
    (prompts_root / "test").mkdir()
    (prompts_root / "test" / "v1.json").write_text(
        json.dumps(
            {
                "name": "test",
                "version": "v1",
                "description": "smoke",
                "system": "sys",
                "user": "hello {{x}}",
            }
        ),
        encoding="utf-8",
    )
    first = get_prompt("test", prompts_root=prompts_root)
    second = get_prompt("test", prompts_root=prompts_root)
    assert first is second


def test_cache_invalidates_on_mtime_change(tmp_path):
    prompts_root = tmp_path / "prompts"
    prompts_root.mkdir()
    (prompts_root / "registry.json").write_text(
        json.dumps({"active": {"test": "v1"}}), encoding="utf-8"
    )
    (prompts_root / "test").mkdir()
    template_path = prompts_root / "test" / "v1.json"
    template_path.write_text(
        json.dumps(
            {
                "name": "test",
                "version": "v1",
                "description": "smoke",
                "system": "sys-original",
                "user": "user",
            }
        ),
        encoding="utf-8",
    )
    first = get_prompt("test", prompts_root=prompts_root)
    assert first.system == "sys-original"

    # Bump the mtime by re-writing with new content. On some platforms
    # mtime resolution is 1s, so sleep just past the boundary — keeps
    # the test deterministic without faking time.
    time.sleep(1.05)
    template_path.write_text(
        json.dumps(
            {
                "name": "test",
                "version": "v1",
                "description": "smoke",
                "system": "sys-updated",
                "user": "user",
            }
        ),
        encoding="utf-8",
    )
    second = get_prompt("test", prompts_root=prompts_root)
    assert second.system == "sys-updated"
    assert first is not second


def test_corrupt_template_file_raises_registry_error(tmp_path):
    prompts_root = tmp_path / "prompts"
    prompts_root.mkdir()
    (prompts_root / "registry.json").write_text(
        json.dumps({"active": {"bad": "v1"}}), encoding="utf-8"
    )
    (prompts_root / "bad").mkdir()
    (prompts_root / "bad" / "v1.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(PromptRegistryError):
        get_prompt("bad", prompts_root=prompts_root)


# ─── registry.json safety checks (post-CodeRabbit review hardening) ──────


def test_registry_with_non_object_top_level_raises(tmp_path):
    """When registry.json's top-level is a list/string/number/null
    instead of an object, fail fast with a clear PromptRegistryError
    rather than silently treating it as empty config. CodeRabbit Major
    finding on PR #5."""
    prompts_root = tmp_path / "prompts"
    prompts_root.mkdir()
    (prompts_root / "registry.json").write_text(
        json.dumps(["not", "an", "object"]),
        encoding="utf-8",
    )
    (prompts_root / "tailoring").mkdir()
    (prompts_root / "tailoring" / "v1.json").write_text(
        json.dumps(
            {
                "name": "tailoring",
                "version": "v1",
                "system": "s",
                "user": "u",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PromptRegistryError) as exc_info:
        get_prompt("tailoring", prompts_root=prompts_root)
    assert "top-level" in str(exc_info.value).lower()


def test_cache_resets_when_switching_to_root_without_registry(tmp_path):
    """Two prompts_root directories:
        - root_with: has registry.json mapping name→v1
        - root_without: no registry.json
    After resolving via root_with, switching to root_without must NOT
    carry the cached active_versions across. The contract is that
    no-registry roots require an explicit version= kwarg; an implicit
    resolution from a stale cache would be a silent correctness bug.
    CodeRabbit Major + Codex P2 on PR #5."""
    # Set up root_with: has a registry.json that points at v1.
    root_with = tmp_path / "with"
    root_with.mkdir()
    (root_with / "registry.json").write_text(
        json.dumps({"active": {"tailoring": "v1"}}),
        encoding="utf-8",
    )
    (root_with / "tailoring").mkdir()
    (root_with / "tailoring" / "v1.json").write_text(
        json.dumps(
            {
                "name": "tailoring",
                "version": "v1",
                "system": "s",
                "user": "u",
            }
        ),
        encoding="utf-8",
    )
    # Set up root_without: has the template at v1 but no registry.json.
    root_without = tmp_path / "without"
    root_without.mkdir()
    (root_without / "tailoring").mkdir()
    (root_without / "tailoring" / "v1.json").write_text(
        json.dumps(
            {
                "name": "tailoring",
                "version": "v1",
                "system": "s-without",
                "user": "u-without",
            }
        ),
        encoding="utf-8",
    )
    # Warm the cache against root_with — this seeds _CACHE.active_versions
    # with {"tailoring": "v1"} keyed against root_with's registry.json.
    template_a = get_prompt("tailoring", prompts_root=root_with)
    assert template_a.system == "s"

    # Now switch to root_without and request the same name WITHOUT
    # passing version=. Before the fix this would have resolved
    # "tailoring" → "v1" from the cached active_versions and silently
    # loaded root_without's tailoring/v1.json. After the fix the
    # missing-manifest reset zeroes the cache for this root, so the
    # implicit resolution must raise PromptNotFoundError.
    with pytest.raises(PromptNotFoundError):
        get_prompt("tailoring", prompts_root=root_without)


def test_template_with_mismatched_name_raises(tmp_path):
    prompts_root = tmp_path / "prompts"
    prompts_root.mkdir()
    (prompts_root / "registry.json").write_text(
        json.dumps({"active": {"correct_name": "v1"}}), encoding="utf-8"
    )
    (prompts_root / "correct_name").mkdir()
    (prompts_root / "correct_name" / "v1.json").write_text(
        json.dumps(
            {
                "name": "wrong_name",  # mismatch with directory name
                "version": "v1",
                "system": "s",
                "user": "u",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PromptRegistryError):
        get_prompt("correct_name", prompts_root=prompts_root)


# ─── shape contract for every shipped prompt ─────────────────────────────


def _shipped_prompts() -> list[tuple[str, str]]:
    """Parse registry.json and return (name, version) pairs the loader
    should be able to find. Parametrizes the shape-contract test
    below."""
    registry_path = PROMPTS_ROOT / "registry.json"
    if not registry_path.exists():
        return []
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    active = payload.get("active", {})
    return sorted(active.items())


@pytest.mark.parametrize("name,version", _shipped_prompts())
def test_shipped_prompt_loads_cleanly(name: str, version: str):
    """Every active prompt in registry.json must load + carry a
    non-empty system + user. Guards against a registry.json that
    points at a missing file or a file with a typo in the name."""
    template = get_prompt(name, version)
    assert isinstance(template, PromptTemplate)
    assert template.name == name
    assert template.version == version
    assert template.system.strip(), f"{name}/{version}: 'system' is empty"
    assert template.user.strip(), f"{name}/{version}: 'user' is empty"


def test_list_active_versions_matches_registry_file():
    active = list_active_versions()
    registry_path = PROMPTS_ROOT / "registry.json"
    raw = json.loads(registry_path.read_text(encoding="utf-8"))
    assert active == raw.get("active", {})


def test_answer_generation_v1_expected_placeholders():
    """Locks the placeholder contract so a v1 edit can't drop
    {{evidence}} without failing the test."""
    template = get_prompt("answer_generation", "v1")
    assert template.placeholders() == {"question", "evidence", "summary_instructions"}


def test_support_status_verifier_v1_expected_placeholders():
    template = get_prompt("support_status_verifier", "v1")
    assert template.placeholders() == {
        "question",
        "answer",
        "reason",
        "claimed_support_status",
        "evidence",
    }


def test_query_router_v1_expected_placeholders():
    template = get_prompt("query_router", "v1")
    assert template.placeholders() == {"intent_type", "evidence_spread", "question"}
