from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException

from src.cloud import create_supabase_client
from src.config import get_settings


@dataclass(frozen=True)
class AuthenticatedUser:
    id: str
    email: str | None = None


def require_authenticated_user(authorization: str | None = Header(default=None)) -> AuthenticatedUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required.")

    token = authorization[len("Bearer ") :].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")

    settings = get_settings()
    client = create_supabase_client(settings.supabase_url, settings.supabase_key)
    response = client.auth.get_user(token)
    user = getattr(response, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")

    user_id = getattr(user, "id", None)
    email = getattr(user, "email", None)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required.")

    return AuthenticatedUser(id=user_id, email=email)
