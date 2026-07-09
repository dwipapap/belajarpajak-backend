"""Smoke tests: auth + RBAC + tenant isolation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import auth_headers

ADMIN_A = "admin@smkn1-pku.local"  # tenant A
ADMIN_B = "admin@pcr.local"  # tenant B
SUPERADMIN = "super@pajaksim.local"
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


def test_create_user_rejects_short_password(client: TestClient) -> None:
    headers = auth_headers(client, ADMIN_A)
    resp = client.post(
        "/api/v1/users",
        headers=headers,
        json={
            "email": "short-password@smkn1-pku.local",
            "password": "short",
            "full_name": "Short Password",
            "role": "siswa",
        },
    )
    assert resp.status_code == 422


def test_update_user_rejects_short_password(client: TestClient) -> None:
    headers = auth_headers(client, ADMIN_A)
    user = client.get("/api/v1/users?size=1", headers=headers).json()["items"][0]
    resp = client.patch(
        f"/api/v1/users/{user['id']}",
        headers=headers,
        json={"password": "short"},
    )
    assert resp.status_code == 422


def test_superadmin_create_user_rejects_invalid_tenant(client: TestClient) -> None:
    headers = auth_headers(client, SUPERADMIN)
    resp = client.post(
        "/api/v1/users",
        headers=headers,
        json={
            "email": "invalid-tenant@example.local",
            "password": "Password123!",
            "full_name": "Invalid Tenant",
            "role": "siswa",
            "tenant_id": 999_999_999,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]


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
