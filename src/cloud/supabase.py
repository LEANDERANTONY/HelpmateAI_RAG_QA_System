from __future__ import annotations

from typing import Any

try:
    from supabase import Client, create_client
except ImportError:  # pragma: no cover - optional dependency in local fallback mode
    Client = Any  # type: ignore[assignment]
    create_client = None


def is_supabase_configured(url: str | None, key: str | None) -> bool:
    return bool((url or "").strip() and (key or "").strip())


def create_supabase_client(url: str | None, key: str | None) -> Client:
    if not is_supabase_configured(url, key):
        raise RuntimeError("Supabase is not configured.")
    if create_client is None:
        raise RuntimeError("Supabase support is not installed. Add the supabase package first.")
    return create_client(str(url).strip(), str(key).strip())


def extract_supabase_rows(response: Any) -> list[dict[str, Any]]:
    if response is None:
        return []
    if isinstance(response, list):
        return [row for row in response if isinstance(row, dict)]
    if isinstance(response, dict):
        data = response.get("data") or []
        return [row for row in data if isinstance(row, dict)]
    data = getattr(response, "data", None) or []
    return [row for row in data if isinstance(row, dict)]
