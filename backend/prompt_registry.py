"""Versioned prompt registry — load templates from JSON, render on demand.

Item 3 of the UX & Iteration Pack. Before this lands, the prompts
that drive answer generation, query routing, support verification,
retrieval planning, and document landmarks were embedded as Python
string literals inside the call sites. That made every prompt tweak
a code change + redeploy, and there was no way to diff "what was the
prompt last week vs today" without spelunking through git blame.

The registry moves each prompt to ``prompts/<name>/v<N>.json`` with
the following shape:

    {
      "name":         "answer_generation",
      "version":      "v1",
      "description":  "Grounded QA prompt for /qa, with optional summary-mode.",
      "system":       "...",
      "user":         "...with {{placeholders}} that callers fill...",
      "schema_ref":   "src.schemas_llm_outputs.AnswerSchema",  # optional
      "notes":        "freeform doc string for future editors"
    }

A top-level ``prompts/registry.json`` lists the active versions so
consumers don't have to enumerate the directory. The registry is the
source of truth — to add a new variant, drop ``v2.json`` next to
``v1.json``, bump ``registry.json`` to point at v2, and the loader's
mtime-based cache invalidates without a process restart.

Render-time API:

    template = get_prompt("answer_generation")
    prompt   = template.render(question=q, evidence=evidence_block)
    # → returns a dict with "system" and "user" keys

Placeholders use ``{{name}}`` (double-brace, Mustache-style) rather
than Python ``str.format`` to keep the JSON readable when prompts
contain literal ``{`` characters (eg. JSON examples inside the
instructions). A missing placeholder raises ``PromptRenderError``
rather than silently producing the literal text — better to surface
the bug than ship a prompt with ``{{evidence}}`` in the user content.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any


logger = logging.getLogger(__name__)


# ``backend/prompts/`` directory. Pin off this module's path so the
# loader works regardless of cwd (eval scripts run from anywhere;
# FastAPI runs from the repo root; tests run from .pytest). The
# location lives under backend/ rather than the repo root because the
# original agent-harness write boundary didn't extend to a top-level
# prompts/ dir; a future refactor can ``mv backend/prompts prompts``
# and bump this constant once that's resolved.
PROMPTS_ROOT = Path(__file__).resolve().parent / "prompts"

# Matches ``{{placeholder_name}}`` — letters, digits, underscores
# only. Anchored so partial matches inside JSON examples don't break.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class PromptRegistryError(RuntimeError):
    """Base for prompt-registry failures. Subclassed by NotFound and
    RenderError so call sites can branch on the specific failure."""


class PromptNotFoundError(PromptRegistryError):
    """Raised when ``get_prompt`` can't find the requested template."""


class PromptRenderError(PromptRegistryError):
    """Raised when ``render`` is missing a placeholder. Distinct from
    NotFoundError so a typo at call time (forgetting to pass
    ``evidence=`` to the answer prompt) doesn't silently ship a
    half-rendered prompt to the model."""


@dataclass(frozen=True)
class PromptTemplate:
    """One template loaded from a JSON file.

    Immutable on purpose — call sites pass instances around freely,
    and the loader's cache holds the same object for the lifetime of
    a file mtime. Mutating it via field assignment would break that.
    """

    name: str
    version: str
    description: str
    system: str
    user: str
    schema_ref: str | None
    notes: str
    # Source path for debugging. Not part of the rendered output.
    source_path: Path

    def placeholders(self) -> set[str]:
        """Return the set of placeholder names across system + user.

        Useful for tests that want to assert "this template still
        expects {question, evidence}" without re-parsing the file.
        """
        found: set[str] = set()
        for text in (self.system, self.user):
            for match in _PLACEHOLDER_RE.finditer(text):
                found.add(match.group(1))
        return found

    def render(self, **kwargs: Any) -> dict[str, str]:
        """Render the template with the supplied placeholders.

        Returns ``{"system": ..., "user": ...}`` — the same shape the
        OpenAI chat completions API expects (the caller fans them
        into messages with the appropriate roles).

        Raises ``PromptRenderError`` if any ``{{name}}`` doesn't have
        a corresponding kwarg.
        """
        return {
            "system": _render_text(self.system, kwargs, where=f"{self.name}.system"),
            "user": _render_text(self.user, kwargs, where=f"{self.name}.user"),
        }


def _render_text(template: str, values: dict[str, Any], *, where: str) -> str:
    missing: list[str] = []

    def _substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            missing.append(key)
            return ""
        return str(values[key])

    rendered = _PLACEHOLDER_RE.sub(_substitute, template)
    if missing:
        unique = sorted(set(missing))
        raise PromptRenderError(
            f"{where}: missing placeholder(s) {unique}; supplied keys = "
            f"{sorted(values)}"
        )
    return rendered


@dataclass
class _RegistryCache:
    """Internal cache state. Guarded by ``_LOCK`` so concurrent
    /qa requests don't race a file read."""

    templates: dict[tuple[str, str], PromptTemplate] = field(default_factory=dict)
    mtimes: dict[Path, float] = field(default_factory=dict)
    active_versions: dict[str, str] = field(default_factory=dict)
    registry_path: Path | None = None
    registry_mtime: float | None = None


_LOCK = RLock()
_CACHE = _RegistryCache()


def _load_registry_manifest(prompts_root: Path) -> dict[str, str]:
    """Read ``prompts/registry.json`` for the active version per name.

    The file is optional — if missing, every ``get_prompt`` call MUST
    pass an explicit ``version`` argument. We still surface a clean
    error in that case rather than silently picking v1.
    """
    registry_path = prompts_root / "registry.json"
    if not registry_path.exists():
        return {}
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromptRegistryError(
            f"prompts/registry.json is malformed: {exc}"
        ) from exc
    active = payload.get("active", {}) if isinstance(payload, dict) else {}
    if not isinstance(active, dict):
        raise PromptRegistryError(
            "prompts/registry.json: 'active' must be an object"
        )
    return {str(name): str(version) for name, version in active.items()}


def _refresh_registry_if_changed(prompts_root: Path) -> None:
    """Reload registry.json if its mtime changed since the last load.

    Cheap (one stat call) and only fires the JSON parse when the file
    actually changed, so warm /qa requests don't pay an I/O cost.
    """
    registry_path = prompts_root / "registry.json"
    if not registry_path.exists():
        if _CACHE.active_versions and _CACHE.registry_path == registry_path:
            # The registry was removed since last load — drop the
            # cached versions so the next call has to specify
            # ``version=`` explicitly.
            _CACHE.active_versions = {}
            _CACHE.registry_mtime = None
        return
    mtime = registry_path.stat().st_mtime
    if _CACHE.registry_path == registry_path and _CACHE.registry_mtime == mtime:
        return
    _CACHE.active_versions = _load_registry_manifest(prompts_root)
    _CACHE.registry_path = registry_path
    _CACHE.registry_mtime = mtime


def _load_template_file(path: Path, *, name: str, version: str) -> PromptTemplate:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromptRegistryError(
            f"prompts/{name}/{version}.json could not be parsed: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise PromptRegistryError(
            f"prompts/{name}/{version}.json: top-level must be an object"
        )
    # File ``name``/``version`` win over directory names so a typo in
    # the JSON surfaces clearly rather than silently masquerading as
    # the directory it lives in.
    json_name = str(payload.get("name", "")).strip()
    json_version = str(payload.get("version", "")).strip()
    if json_name and json_name != name:
        raise PromptRegistryError(
            f"prompts/{name}/{version}.json: 'name' field is {json_name!r}; "
            f"directory expects {name!r}"
        )
    if json_version and json_version != version:
        raise PromptRegistryError(
            f"prompts/{name}/{version}.json: 'version' field is {json_version!r}; "
            f"filename expects {version!r}"
        )
    system = payload.get("system")
    user = payload.get("user")
    if not isinstance(system, str) or not isinstance(user, str):
        raise PromptRegistryError(
            f"prompts/{name}/{version}.json: 'system' and 'user' must be strings"
        )
    return PromptTemplate(
        name=name,
        version=version,
        description=str(payload.get("description", "")),
        system=system,
        user=user,
        schema_ref=(str(payload["schema_ref"]) if "schema_ref" in payload and payload["schema_ref"] else None),
        notes=str(payload.get("notes", "")),
        source_path=path,
    )


def get_prompt(
    name: str,
    version: str | None = None,
    *,
    prompts_root: Path | None = None,
) -> PromptTemplate:
    """Load a prompt template by name (+ optional explicit version).

    When ``version`` is None the registry.json manifest decides which
    version is active. Cached per-(name, version) pair with mtime
    invalidation: editing a JSON file picks up on the next call without
    a restart, which keeps the dev loop tight when iterating on
    prompts.
    """
    root = prompts_root or PROMPTS_ROOT
    with _LOCK:
        _refresh_registry_if_changed(root)
        resolved_version = version or _CACHE.active_versions.get(name)
        if not resolved_version:
            raise PromptNotFoundError(
                f"No active version configured for prompt {name!r} (and no "
                f"explicit version supplied). Add an entry to "
                f"prompts/registry.json or pass version=..."
            )
        path = root / name / f"{resolved_version}.json"
        if not path.exists():
            raise PromptNotFoundError(
                f"Prompt file does not exist: {path}"
            )
        mtime = path.stat().st_mtime
        cache_key = (name, resolved_version)
        cached = _CACHE.templates.get(cache_key)
        if cached is not None and _CACHE.mtimes.get(path) == mtime:
            return cached
        template = _load_template_file(path, name=name, version=resolved_version)
        _CACHE.templates[cache_key] = template
        _CACHE.mtimes[path] = mtime
        return template


def render_prompt(
    name: str,
    *,
    version: str | None = None,
    prompts_root: Path | None = None,
    **placeholders: Any,
) -> dict[str, str]:
    """Convenience: ``get_prompt(name, version).render(**placeholders)``.

    Returns the rendered ``{"system": ..., "user": ...}`` payload —
    most call sites want this shape directly to feed into the OpenAI
    chat completions ``messages`` array.
    """
    return get_prompt(name, version, prompts_root=prompts_root).render(**placeholders)


def clear_cache() -> None:
    """Drop all cached templates + the registry manifest cache.

    Test-only — production never needs this because mtime
    invalidation handles editor reloads transparently. Tests call it
    in tearDown so a registry rebuild on one test doesn't leak its
    state into another."""
    with _LOCK:
        _CACHE.templates.clear()
        _CACHE.mtimes.clear()
        _CACHE.active_versions.clear()
        _CACHE.registry_path = None
        _CACHE.registry_mtime = None


def list_active_versions(*, prompts_root: Path | None = None) -> dict[str, str]:
    """Return a copy of the active version map. Inspecting state for
    tooling (eval scripts, debug routes) — never mutate the returned
    dict; it's a snapshot at the time of the call."""
    root = prompts_root or PROMPTS_ROOT
    with _LOCK:
        _refresh_registry_if_changed(root)
        return dict(_CACHE.active_versions)
