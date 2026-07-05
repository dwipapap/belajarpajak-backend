"""Smoke tests: auth + RBAC + tenant isolation (Phase 1 quality bar §10)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import auth_headers

ADMIN_A = "admin@smkn1-pku.local"  # tenant A
ADMIN_B = "admin@pcr.local"  # tenant B
SISWA_A = "siswa1@smkn1-pku.local"


def test_login_success_returns_tokens(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": ADMIN_A, "password": "Password123!"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"


def test_login_wrong_password_returns_401(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": ADMIN_A, "password": "wrong-password"},
    )
    assert resp.status_code == 401


def test_admin_cannot_see_other_tenant_users(client: TestClient) -> None:
    """Admin of tenant A must never see tenant B users in the list."""
    headers_a = auth_headers(client, ADMIN_A)
    headers_b = auth_headers(client, ADMIN_B)

    # Resolve each admin's own tenant id via /me.
    me_a = client.get("/api/v1/auth/me", headers=headers_a).json()
    me_b = client.get("/api/v1/auth/me", headers=headers_b).json()
    assert me_a["tenant_id"] != me_b["tenant_id"]

    listing = client.get("/api/v1/users?size=100", headers=headers_a).json()
    tenant_ids = {u["tenant_id"] for u in listing["items"]}
    assert tenant_ids == {me_a["tenant_id"]}
    assert me_b["tenant_id"] not in tenant_ids


def test_siswa_cannot_create_users(client: TestClient) -> None:
    headers = auth_headers(client, SISWA_A)
    resp = client.post(
        "/api/v1/users",
        headers=headers,
        json={
            "email": "hacker@smkn1-pku.local",
            "password": "Password123!",
            "full_name": "Nope",
            "role": "siswa",
        },
    )
    assert resp.status_code == 403


def test_refresh_returns_new_access_token(client: TestClient) -> None:
    tokens = client.post(
        "/api/v1/auth/login",
        json={"email": ADMIN_A, "password": "Password123!"},
    ).json()
    resp = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert resp.status_code == 200
    assert resp.json()["access_token"]
