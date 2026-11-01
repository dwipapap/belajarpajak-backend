"""PATCH /tenants/{id} with explicit berlaku_dari / berlaku_sampai."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.routers.tenants import _add_months
from tests.conftest import auth_headers

BASE = "/api/v1"
SUPERADMIN = "super@pajaksim.local"


def _make_tenant(client: TestClient, months: int = 12) -> tuple[dict, dict[str, str]]:
    super_headers = auth_headers(client, SUPERADMIN)
    suffix = uuid.uuid4().hex[:6]
    res = client.post(
        f"{BASE}/tenants",
        json={
            "name": "Sekolah Tanggal",
            "slug": f"sekolah-tanggal-{suffix}",
            "type": "smk",
            "masa_berlaku_bulan": months,
            "admin": {
                "email": f"admin.tanggal-{suffix}@simulasi.local",
                "full_name": "Admin Tanggal",
            },
        },
        headers=super_headers,
    )
    assert res.status_code == 201, res.text
    return res.json(), super_headers


def test_explicit_dates_ok(client: TestClient):
    tenant, super_headers = _make_tenant(client)
    dari = date.today().isoformat()
    sampai = (date.today() + timedelta(days=90)).isoformat()
    res = client.patch(
        f"{BASE}/tenants/{tenant['id']}",
        json={"berlaku_dari": dari, "berlaku_sampai": sampai},
        headers=super_headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["berlaku_dari"] == dari
    assert body["berlaku_sampai"] == sampai


def test_explicit_single_side_keeps_other(client: TestClient):
    tenant, super_headers = _make_tenant(client)
    new_sampai = (date.today() + timedelta(days=60)).isoformat()
    res = client.patch(
        f"{BASE}/tenants/{tenant['id']}",
        json={"berlaku_sampai": new_sampai},
        headers=super_headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["berlaku_sampai"] == new_sampai
    assert body["berlaku_dari"] == tenant["berlaku_dari"]


def test_dari_after_sampai_422(client: TestClient):
    tenant, super_headers = _make_tenant(client)
    res = client.patch(
        f"{BASE}/tenants/{tenant['id']}",
        json={
            "berlaku_dari": (date.today() + timedelta(days=10)).isoformat(),
            "berlaku_sampai": date.today().isoformat(),
        },
        headers=super_headers,
    )
    assert res.status_code == 422, res.text


def test_sampai_yesterday_422(client: TestClient):
    tenant, super_headers = _make_tenant(client)
    res = client.patch(
        f"{BASE}/tenants/{tenant['id']}",
        json={"berlaku_sampai": (date.today() - timedelta(days=1)).isoformat()},
        headers=super_headers,
    )
    assert res.status_code == 422, res.text
    assert "Nonaktifkan" in res.json()["detail"]


def test_explicit_dates_win_over_months(client: TestClient):
    tenant, super_headers = _make_tenant(client)
    dari = date.today().isoformat()
    sampai = (date.today() + timedelta(days=30)).isoformat()
    res = client.patch(
        f"{BASE}/tenants/{tenant['id']}",
        json={
            "berlaku_dari": dari,
            "berlaku_sampai": sampai,
            "masa_berlaku_bulan": 12,
        },
        headers=super_headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["berlaku_dari"] == dari
    assert body["berlaku_sampai"] == sampai
    # Must NOT equal the relative computation (today/max+sampai + 12 months).
    assert body["berlaku_sampai"] != _add_months(date.today(), 12).isoformat()
