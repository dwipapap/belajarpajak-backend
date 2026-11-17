"""Role dashboards: guru progress + grading queue, siswa to-do, tenant/platform overview."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.models.billing import BillingCode
from app.models.enums import (
    BillingStatus,
    SlipStatus,
    SlipTaxNature,
    SlipType,
    TaxReturnStatus,
    TaxReturnType,
)
from app.models.school_class import SchoolClass
from app.models.slip import WithholdingSlip
from app.models.tax_return import TaxReturn
from app.models.user import User
from tests.conftest import auth_headers

BASE = "/api/v1/dashboard"
GURU = "guru1@smkn1-pku.local"
GURU2 = "guru2@smkn1-pku.local"
SISWA = "siswa1@smkn1-pku.local"
ADMIN = "admin@smkn1-pku.local"
SUPERADMIN = "super@pajaksim.local"
PCR_ADMIN = "admin@pcr.local"


@pytest.fixture()
def seeded_docs():
    """One issued ungraded slip, one draft slip, one reported ungraded SPT, one unpaid billing."""
    with Session(engine) as s:
        siswa = s.exec(select(User).where(User.email == SISWA)).one()
        guru = s.exec(select(User).where(User.email == GURU)).one()
        cls = s.exec(select(SchoolClass).where(SchoolClass.guru_id == guru.id)).first()
        slip_kwargs = dict(
            tenant_id=siswa.tenant_id,
            class_id=cls.id,
            siswa_id=siswa.id,
            created_by_id=siswa.id,
            tax_month=7,
            tax_year=2026,
            tax_object_code="21-100-01",
            tax_nature=SlipTaxNature.non_final,
            recipient_name="DASHBOARD TEST",
        )
        issued = WithholdingSlip(slip_type=SlipType.bp21, status=SlipStatus.issued, **slip_kwargs)
        draft = WithholdingSlip(slip_type=SlipType.bppu, status=SlipStatus.draft, **slip_kwargs)
        spt = TaxReturn(
            tenant_id=siswa.tenant_id,
            class_id=cls.id,
            siswa_id=siswa.id,
            created_by_id=siswa.id,
            return_type=TaxReturnType.ppn,
            status=TaxReturnStatus.reported,
            tax_month=7,
            tax_year=2026,
        )
        billing = BillingCode(
            tenant_id=siswa.tenant_id,
            siswa_id=siswa.id,
            code="999000111222333",
            status=BillingStatus.unpaid,
            amount=123_000,
            masa_pajak="072026",
        )
        s.add_all([issued, draft, spt, billing])
        s.commit()
        ids = [issued, draft, spt, billing]
        for o in ids:
            s.refresh(o)
        info = {
            "class_id": cls.id,
            "siswa_id": siswa.id,
            "slip_id": issued.id,
            "draft_id": draft.id,
            "spt_id": spt.id,
        }
        refs = [(type(o), o.id) for o in ids]
    yield info
    with Session(engine) as s:
        for model, oid in refs:
            obj = s.get(model, oid)
            if obj is not None:
                s.delete(obj)
        s.commit()


def test_guru_dashboard_progress_and_queue(client: TestClient, seeded_docs):
    res = client.get(f"{BASE}/guru", headers=auth_headers(client, GURU))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["classes"], body
    res = client.get(
        f"{BASE}/guru",
        params={"class_id": seeded_docs["class_id"]},
        headers=auth_headers(client, GURU),
    )
    body = res.json()
    assert body["class_id"] == seeded_docs["class_id"]
    row = next(r for r in body["students"] if r["id"] == seeded_docs["siswa_id"])
    assert row["ebupot_issued"] >= 1
    assert row["ebupot_draft"] >= 1
    assert row["spt_reported"] >= 1 and row["spt_ppn"] >= 1
    assert row["has_activity"] is True and row["last_activity_at"]
    mods = {m["module"]: m["count"] for m in body["grading_by_module"]}
    assert mods.get("bp21", 0) >= 1 and mods.get("spt", 0) >= 1
    queue = {(i["module"], i["id"]) for i in body["grading_items"]}
    assert ("bp21", seeded_docs["slip_id"]) in queue
    assert not any(m == "bppu" and i == seeded_docs["draft_id"] for m, i in queue)  # draft
    assert ("spt", seeded_docs["spt_id"]) in queue
    assert body["grading_total"] == sum(mods.values())


def test_guru_dashboard_graded_item_leaves_queue(client: TestClient, seeded_docs):
    headers = auth_headers(client, GURU)
    r = client.patch(
        f"/api/v1/bp21/{seeded_docs['slip_id']}/review",
        json={"score": 90, "teacher_feedback": "Baik"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = client.get(
        f"{BASE}/guru", params={"class_id": seeded_docs["class_id"]}, headers=headers
    ).json()
    assert ("bp21", seeded_docs["slip_id"]) not in {
        (i["module"], i["id"]) for i in body["grading_items"]
    }
    siswa = client.get(f"{BASE}/siswa", headers=auth_headers(client, SISWA)).json()
    assert any(d["id"] == seeded_docs["slip_id"] and d["score"] == 90 for d in siswa["graded"])
    assert siswa["grades"]["reviewed"] >= 1 and siswa["grades"]["average"] is not None


def test_guru_cannot_read_other_gurus_class(client: TestClient, seeded_docs):
    res = client.get(
        f"{BASE}/guru",
        params={"class_id": seeded_docs["class_id"]},
        headers=auth_headers(client, GURU2),
    )
    assert res.status_code == 404


def test_guru_dashboard_role_guard(client: TestClient):
    assert client.get(f"{BASE}/guru", headers=auth_headers(client, SISWA)).status_code == 403
    assert client.get(f"{BASE}/guru", headers=auth_headers(client, ADMIN)).status_code == 403
    assert client.get(f"{BASE}/guru").status_code == 401


def test_siswa_dashboard_todo_and_counts(client: TestClient, seeded_docs):
    res = client.get(f"{BASE}/siswa", headers=auth_headers(client, SISWA))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ebupot"]["draft"] >= 1 and body["ebupot"]["issued"] >= 1
    assert body["ebupot_by_type"]["bppu"]["draft"] >= 1
    assert body["spt"]["reported"] >= 1
    assert body["billing"]["unpaid"] >= 1 and body["billing"]["unpaid_amount"] >= 123_000
    kinds = {t["kind"] for t in body["todos"]}
    assert {"draft", "billing"} <= kinds
    assert body["todo_total"] >= 2
    assert body["recent"]


def test_siswa_dashboard_is_own_data_only(client: TestClient, seeded_docs):
    other = client.get(f"{BASE}/siswa", headers=auth_headers(client, "siswa4@smkn1-pku.local"))
    assert other.status_code == 200
    assert other.json()["billing"]["unpaid_amount"] == 0 or other.json()["billing"]["unpaid"] == 0
    assert client.get(f"{BASE}/siswa", headers=auth_headers(client, GURU)).status_code == 403


def test_tenant_overview_for_admin(client: TestClient):
    res = client.get(f"{BASE}/tenant", headers=auth_headers(client, ADMIN))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["slug"] == "smkn1-pku"
    assert body["guru"]["used"] >= 1 and body["siswa"]["used"] >= 1
    assert body["active_classes"] <= body["kelas"]["used"]
    assert client.get(f"{BASE}/tenant", headers=auth_headers(client, GURU)).status_code == 403
    assert client.get(f"{BASE}/tenant", headers=auth_headers(client, SUPERADMIN)).status_code == 403


def test_platform_dashboard_superadmin_only(client: TestClient):
    res = client.get(f"{BASE}/platform", headers=auth_headers(client, SUPERADMIN))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["tenants_total"] == len(body["tenants"]) >= 2
    assert {t["slug"] for t in body["tenants"]} >= {"smkn1-pku"}
    assert client.get(f"{BASE}/platform", headers=auth_headers(client, ADMIN)).status_code == 403
    assert (
        client.get(f"{BASE}/platform", headers=auth_headers(client, PCR_ADMIN)).status_code == 403
    )
