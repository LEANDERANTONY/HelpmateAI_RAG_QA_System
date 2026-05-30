"""POST /billing/portal branches (M25).

The self-service entry to subscription management. Four documented outcomes
(503 no API key, 404 no LS customer, 502 LS returns no URL, 200 with the url)
had no coverage.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.auth import AuthenticatedUser, require_authenticated_user
from backend.main import app

USER = "00000000-0000-4000-8000-0000000000b1"


@pytest.fixture
def client():
    app.dependency_overrides[require_authenticated_user] = lambda: AuthenticatedUser(
        id=USER, email="b@example.com"
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(require_authenticated_user, None)


def test_portal_503_when_api_key_unset(client, monkeypatch):
    from backend import billing_routes

    monkeypatch.setattr(billing_routes, "_ls_api_key", lambda: None)
    assert client.post("/billing/portal").status_code == 503


def test_portal_404_when_no_subscription(client, monkeypatch):
    import backend.subscriptions as subs
    from backend import billing_routes

    monkeypatch.setattr(billing_routes, "_ls_api_key", lambda: "test-key")
    monkeypatch.setattr(subs, "get_active_subscription", lambda _uid: None)
    assert client.post("/billing/portal").status_code == 404


def test_portal_502_when_no_url_returned(client, monkeypatch):
    import backend.subscriptions as subs
    from backend import billing_routes

    monkeypatch.setattr(billing_routes, "_ls_api_key", lambda: "test-key")
    monkeypatch.setattr(
        subs, "get_active_subscription", lambda _uid: SimpleNamespace(processor_customer_id="cust_1")
    )
    monkeypatch.setattr(billing_routes, "_fetch_customer_portal_url", lambda **_kw: "")
    assert client.post("/billing/portal").status_code == 502


def test_portal_200_returns_url(client, monkeypatch):
    import backend.subscriptions as subs
    from backend import billing_routes

    monkeypatch.setattr(billing_routes, "_ls_api_key", lambda: "test-key")
    monkeypatch.setattr(
        subs, "get_active_subscription", lambda _uid: SimpleNamespace(processor_customer_id="cust_1")
    )
    monkeypatch.setattr(
        billing_routes, "_fetch_customer_portal_url", lambda **_kw: "https://portal.example/abc"
    )
    response = client.post("/billing/portal")
    assert response.status_code == 200
    assert response.json()["url"] == "https://portal.example/abc"
