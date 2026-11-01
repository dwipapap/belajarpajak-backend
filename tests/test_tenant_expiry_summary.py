"""Tenant expiry + summary dossier + PATCH name/type/slug guard."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session

from tests.conftest import auth_headers

BASE = "/api/v1"
SUPERADMIN = "super@pajaksim.local"


def _make_tenant(client: TestClient) -> tuple[dict, dict[str, str]]:
    super_headers = auth_headers(client, SUPERADMIN)
    suffix = uuid.uuid4().hex[:6]
    res = client.post(
        f"{BASE}/tenants",
        json={
            "name": "Sekolah Dossier",
            "slug": f"sekolah-dossier-{suffix}",
            "type": "smk",
            "masa_berlaku_bulan": 12,
            "max_guru": 5,
            "max_siswa": 10,
            "max_kelas": 3,
            "admin": {
                "email": f"admin.dossier-{suffix}@simulasi.local",
                "full_name": "Admin Dossier",
            },
        },
        headers=super_headers,
    )
    assert res.status_code == 201, res.text
    return res.json(), super_headers


def test_expired_tenant_blocks_login_and_refresh(client: TestClient):
    from app.db import engine
    from app.models.tenant import Tenant

    tenant, super_headers = _make_tenant(client)
    admin_email = client.get(
        f"{BASE}/users", params={"tenant_id": tenant["id"]}, headers=super_headers
    ).json()["items"][0]["email"]

    # Force expiry directly in the DB (API only extends validity forward).
    with Session(engine) as session:
        db_tenant = session.get(Tenant, tenant["id"])
        db_tenant.berlaku_sampai = date.today() - timedelta(days=1)
        session.add(db_tenant)
        session.commit()

    denied = client.post(
        f"{BASE}/auth/login",
        json={"identifier": admin_email, "password": "Password123!"},
    )
    assert denied.status_code == 403
    assert "Masa berlaku instansi telah habis" in denied.json()["detail"]

    # Refresh is blocked too: grab a pre-expiry refresh token by resetting
    # validity briefly, then expire again before refreshing.
    with Session(engine) as session:
        db_tenant = session.get(Tenant, tenant["id"])
        db_tenant.berlaku_sampai = date.today() + timedelta(days=30)
        session.add(db_tenant)
        session.commit()
    tokens = client.post(
        f"{BASE}/auth/login",
        json={"identifier": admin_email, "password": "Password123!"},
    )
    assert tokens.status_code == 200
    with Session(engine) as session:
        db_tenant = session.get(Tenant, tenant["id"])
        db_tenant.berlaku_sampai = date.today() - timedelta(days=1)
        session.add(db_tenant)
        session.commit()
    denied_refresh = client.post(
        f"{BASE}/auth/refresh",
        json={"refresh_token": tokens.json()["refresh_token"]},
    )
    assert denied_refresh.status_code == 403
    assert "Masa berlaku instansi telah habis" in denied_refresh.json()["detail"]

    # Superadmin (no tenant) is unaffected.
    me = client.get(f"{BASE}/auth/me", headers=super_headers)
    assert me.status_code == 200


def test_tenant_summary_numbers(client: TestClient):
    tenant, super_headers = _make_tenant(client)

    res = client.get(f"{BASE}/tenants/{tenant['id']}/summary", headers=super_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["tenant_id"] == tenant["id"]
    assert body["guru"] == {"used": 0, "limit": 5, "full": False}
    assert body["siswa"]["used"] == 0 and body["siswa"]["limit"] == 10
    assert body["kelas"]["used"] == 0 and body["kelas"]["limit"] == 3
    assert body["admin"]["email"].startswith("admin.dossier-")
    assert body["is_active"] is True
    assert body["days_remaining"] is not None and body["days_remaining"] > 0
    assert body["expired"] is False

    # Non-superadmin cannot read the dossier.
    admin_headers = auth_headers(client, body["admin"]["email"])
    denied = client.get(f"{BASE}/tenants/{tenant['id']}/summary", headers=admin_headers)
    assert denied.status_code == 403


def test_patch_name_type_ok_slug_rejected(client: TestClient):
    tenant, super_headers = _make_tenant(client)

    res = client.patch(
        f"{BASE}/tenants/{tenant['id']}",
        json={"name": "Nama Baru Sekolah", "type": "kampus"},
        headers=super_headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["name"] == "Nama Baru Sekolah"
    assert res.json()["type"] == "kampus"

    res = client.patch(
        f"{BASE}/tenants/{tenant['id']}",
        json={"slug": "slug-baru"},
        headers=super_headers,
    )
    assert res.status_code == 422
    assert "Slug" in res.json()["detail"]
