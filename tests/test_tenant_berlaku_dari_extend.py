"""Tenant berlaku_dari + extend semantics (max(sampai, today) + N months)."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session

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
            "name": "Sekolah Periode",
            "slug": f"sekolah-periode-{suffix}",
            "type": "smk",
            "masa_berlaku_bulan": months,
            "admin": {
                "email": f"admin.periode-{suffix}@simulasi.local",
                "full_name": "Admin Periode",
            },
        },
        headers=super_headers,
    )
    assert res.status_code == 201, res.text
    return res.json(), super_headers


def test_create_fills_dari_and_sampai(client: TestClient):
    tenant, _ = _make_tenant(client, 12)
    today = date.today()
    assert tenant["berlaku_dari"] == today.isoformat()
    assert tenant["berlaku_sampai"] == _add_months(today, 12).isoformat()


def test_extend_before_expiry_preserves_remainder(client: TestClient):
    from app.db import engine
    from app.models.tenant import Tenant

    tenant, super_headers = _make_tenant(client, 12)
    # Simulate 2 months already consumed: move the end date forward only
    # 10 months from today, then extend by 6 months.
    with Session(engine) as session:
        db_tenant = session.get(Tenant, tenant["id"])
        db_tenant.berlaku_sampai = _add_months(date.today(), 10)
        db_tenant.berlaku_dari = date.today() - timedelta(days=60)
        session.add(db_tenant)
        session.commit()
        old_sampai = db_tenant.berlaku_sampai
        old_dari = db_tenant.berlaku_dari

    res = client.patch(
        f"{BASE}/tenants/{tenant['id']}",
        json={"masa_berlaku_bulan": 6},
        headers=super_headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["berlaku_sampai"] == _add_months(old_sampai, 6).isoformat()
    assert body["berlaku_dari"] == old_dari.isoformat()


def test_extend_after_expiry_starts_new_period(client: TestClient):
    from app.db import engine
    from app.models.tenant import Tenant

    tenant, super_headers = _make_tenant(client, 12)
    with Session(engine) as session:
        db_tenant = session.get(Tenant, tenant["id"])
        db_tenant.berlaku_sampai = date.today() - timedelta(days=1)
        db_tenant.berlaku_dari = date.today() - timedelta(days=400)
        session.add(db_tenant)
        session.commit()

    res = client.patch(
        f"{BASE}/tenants/{tenant['id']}",
        json={"masa_berlaku_bulan": 3},
        headers=super_headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    today = date.today()
    assert body["berlaku_dari"] == today.isoformat()
    assert body["berlaku_sampai"] == _add_months(today, 3).isoformat()


def test_summary_includes_berlaku_dari(client: TestClient):
    tenant, super_headers = _make_tenant(client, 12)
    res = client.get(f"{BASE}/tenants/{tenant['id']}/summary", headers=super_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["berlaku_dari"] == tenant["berlaku_dari"]
    assert body["berlaku_sampai"] == tenant["berlaku_sampai"]
    assert body["days_remaining"] == (
        date.fromisoformat(body["berlaku_sampai"]) - date.today()
    ).days


def test_add_months_end_of_month():
    assert _add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert _add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
