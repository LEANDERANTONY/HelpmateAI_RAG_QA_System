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

import hashlib
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
    assert template.version == "v2"
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


def test_template_cache_does_not_cross_pollute_between_roots(tmp_path):
    """Two prompts_root directories that ship the SAME (name, version)
    must NOT share a template cache entry — even when both files
    happen to be readable and parse cleanly. CodeRabbit Major + Codex
    P2 round-2 on PR #5: the original cache key was just
    (name, version) so a template loaded from root_a could be returned
    when the caller asked from root_b with the same key.

    The fix scopes the cache by ``(prompts_root.resolve(), name, version)``
    so different roots are distinct cache entries.
    """
    root_a = tmp_path / "a"
    root_a.mkdir()
    (root_a / "registry.json").write_text(
        json.dumps({"active": {"tailoring": "v1"}}),
        encoding="utf-8",
    )
    (root_a / "tailoring").mkdir()
    (root_a / "tailoring" / "v1.json").write_text(
        json.dumps(
            {
                "name": "tailoring",
                "version": "v1",
                "system": "from-root-a",
                "user": "u-a",
            }
        ),
        encoding="utf-8",
    )

    root_b = tmp_path / "b"
    root_b.mkdir()
    (root_b / "registry.json").write_text(
        json.dumps({"active": {"tailoring": "v1"}}),
        encoding="utf-8",
    )
    (root_b / "tailoring").mkdir()
    (root_b / "tailoring" / "v1.json").write_text(
        json.dumps(
            {
                "name": "tailoring",
                "version": "v1",
                "system": "from-root-b",
                "user": "u-b",
            }
        ),
        encoding="utf-8",
    )

    template_a = get_prompt("tailoring", prompts_root=root_a)
    template_b = get_prompt("tailoring", prompts_root=root_b)
    assert template_a.system == "from-root-a"
    assert template_b.system == "from-root-b", (
        "cache must NOT serve root_a's template when asked from root_b"
    )

    # Round-trip back to root_a to make sure adding root_b's entry
    # didn't evict root_a's cached template either.
    template_a_again = get_prompt("tailoring", prompts_root=root_a)
    assert template_a_again.system == "from-root-a"


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


def test_system_messages_match_call_sites_in_src():
    """``src/generation/service.py`` and ``src/query_router.py`` pass
    hardcoded system messages when invoking the LLM (the user prompt
    now comes from the registry, but the system message is wired at
    the call site). Locks those strings to the registry's ``system``
    fields so a future tweak to one without the other doesn't silently
    drift.

    The contract: the registry's ``system`` field is the canonical
    source of truth; the call-site strings MUST equal it.
    """
    expected = {
        "answer_generation": "You answer questions using only supplied document evidence.",
        "support_verifier": "You strictly verify whether answers are fully supported by supplied document evidence.",
        "support_status_verifier": "You classify answer support status using only supplied document evidence.",
        "query_router": "You classify retrieval routes for a document QA pipeline.",
    }
    for name, call_site_system in expected.items():
        template = get_prompt(name, "v1")
        assert template.system == call_site_system, (
            f"{name}/v1.json system drifted from the hardcoded string in "
            f"src/ (registry={template.system!r}, call_site={call_site_system!r}). "
            f"Update either the JSON or the call site so they agree."
        )


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


# ─── byte-identity drift guard ──────────────────────────────────────────
#
# The PR#5 prompt-registry migration shipped WITHOUT a drift guard:
# nothing asserted the migrated JSON text matched its pre-registry
# inline form, so a future edit to any v1.json could silently change
# /qa behaviour (e.g. drop a required JSON key the strict AnswerOutput /
# verifier schemas demand -> universal StructuredOutputError ->
# universal abstention). These digests pin the exact, audited-good
# system+user text of every active prompt.
#
# If a prompt change is INTENTIONAL:
#   1. bump that prompt's version in backend/prompts/registry.json
#      (ship vN+1, keep vN immutable), and
#   2. regenerate the digest below for the new active version.
# A failure here with no such bump means a prompt drifted silently —
# treat it as a release blocker, not a test to "just update".
_FROZEN_PROMPT_DIGESTS = {
    "answer_generation": "6514a647bdd9872f296814826f6ab7c1236d5acfac2cf5d76cd1d915c2163d87",
    "document_landmarks": "c8ec8d14308168870ba75b81ae4990b617800753470b769e54484c9d79472e31",
    "planner": "73ea5174a07b7e7a651eb182294094b01edff814dccba042d15669c5b3b4b7a2",
    "query_router": "efe7541635ca37e51a24861a2f0a9aff0742a510b493e474372c73f29dcae8b6",
    "retrieval_orchestrator": "5a217d034bf51a30628ee5508a4f51ad601a2fdc55df607b942ea225d9c0dbc6",
    "support_status_verifier": "0125dba3c9a4e9a1a8197ba2efd10234fab7089057f28079d581fb0d814d590c",
    "support_verifier": "b253edde6389cc26cf62b25b7d52ebbdad106c9e1ea12a1e3896b50b994085c3",
}


def _prompt_digest(template: PromptTemplate) -> str:
    # \x1e (record separator) between system and user so a string
    # can't migrate across the boundary undetected.
    blob = f"{template.system}\x1e{template.user}".encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def test_active_registry_matches_frozen_set():
    """Every prompt that is active in registry.json must have a frozen
    digest — a new prompt added without pinning it is a coverage gap."""
    active = set(list_active_versions().keys())
    assert active == set(_FROZEN_PROMPT_DIGESTS), (
        "Active prompts and frozen-digest set diverged. Pin the new "
        "prompt's digest in _FROZEN_PROMPT_DIGESTS (or remove the stale "
        f"entry). active={sorted(active)} "
        f"frozen={sorted(_FROZEN_PROMPT_DIGESTS)}"
    )


@pytest.mark.parametrize("name", sorted(_FROZEN_PROMPT_DIGESTS))
def test_prompt_text_has_not_drifted(name):
    template = get_prompt(name)
    actual = _prompt_digest(template)
    assert actual == _FROZEN_PROMPT_DIGESTS[name], (
        f"Prompt '{name}' (active v{template.version}) text drifted from "
        f"its pinned digest. If intentional: bump the version in "
        f"registry.json and update _FROZEN_PROMPT_DIGESTS. If not: a "
        f"prompt changed silently — block the release. "
        f"expected={_FROZEN_PROMPT_DIGESTS[name]} actual={actual}"
    )
