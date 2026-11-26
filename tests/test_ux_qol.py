"""Integration tests for Global, Guru & Siswa QoL backend enhancements."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.models.assignment import Assignment, AssignmentSubmission
from app.models.audit_log import AuditLog
from app.models.school_class import SchoolClass
from app.models.slip import WithholdingSlip
from app.models.user import User
from tests.conftest import auth_headers

BASE = "/api/v1"
ADMIN_A = "admin@smkn1-pku.local"
GURU_1_A = "guru1@smkn1-pku.local"
GURU_2_A = "guru2@smkn1-pku.local"
SISWA_1_A = "siswa1@smkn1-pku.local"
SISWA_2_A = "siswa2@smkn1-pku.local"


def _get_class_id(guru_email: str) -> int:
    with Session(engine) as session:
        guru = session.exec(select(User).where(User.email == guru_email)).one()
        sc = session.exec(
            select(SchoolClass).where(SchoolClass.guru_id == guru.id)
        ).first()
        assert sc is not None
        return sc.id


def _get_user(email: str) -> User:
    with Session(engine) as session:
        return session.exec(select(User).where(User.email == email)).one()


def test_case_insensitive_username_login(client: TestClient) -> None:
    """Students typing lowercase/mixed-case username match uppercase NIS00000001."""
    for identifier in ("nis00000001", "NiS00000001"):
        res = client.post(
            f"{BASE}/auth/login",
            json={"identifier": identifier, "password": "Password123!"},
        )
        assert res.status_code == 200, res.text
        me = client.get(
            f"{BASE}/auth/me",
            headers={"Authorization": f"Bearer {res.json()['access_token']}"},
        )
        assert me.status_code == 200
        assert me.json()["username"] == "NIS00000001"
        assert me.json()["role"] == "siswa"


def test_document_lists_include_siswa_name_and_filter_by_q(client: TestClient) -> None:
    """GET bppu, faktur-keluaran, and faktur-masukan include siswa_name and support q search."""
    siswa1 = _get_user(SISWA_1_A)
    siswa2 = _get_user(SISWA_2_A)
    siswa1_headers = auth_headers(client, SISWA_1_A)
    siswa2_headers = auth_headers(client, SISWA_2_A)
    guru_headers = auth_headers(client, GURU_1_A)
    class1_id = _get_class_id(GURU_1_A)
    tag = uuid.uuid4().hex[:6].upper()

    # 1. BPPU: create for siswa1 and siswa2 with unique recipient_name
    bppu1 = client.post(
        f"{BASE}/bppu",
        json={
            "class_id": class1_id,
            "tax_month": 7,
            "tax_year": 2026,
            "recipient_identity_number": "1234567890123456",
            "recipient_name": f"PT BPPU SATU {tag}",
            "tax_object_code": "22-100-07",
            "tax_nature": "non_final",
            "tax_facility": "none",
            "dpp": 10_000_000,
            "rate_percent": 0.25,
            "document_date": "2026-07-01",
        },
        headers=siswa1_headers,
    )
    assert bppu1.status_code == 201, bppu1.text
    bppu1_id = bppu1.json()["id"]

    bppu2 = client.post(
        f"{BASE}/bppu",
        json={
            "class_id": class1_id,
            "tax_month": 7,
            "tax_year": 2026,
            "recipient_identity_number": "1234567890123456",
            "recipient_name": f"PT BPPU DUA {tag}",
            "tax_object_code": "22-100-07",
            "tax_nature": "non_final",
            "tax_facility": "none",
            "dpp": 5_000_000,
            "rate_percent": 0.25,
            "document_date": "2026-07-02",
        },
        headers=siswa2_headers,
    )
    assert bppu2.status_code == 201, bppu2.text

    # Filter BPPU by counterparty name
    res_bppu_cp = client.get(
        f"{BASE}/bppu", params={"q": f"BPPU SATU {tag}"}, headers=guru_headers
    )
    assert res_bppu_cp.status_code == 200
    bppu_items = res_bppu_cp.json()["items"]
    assert len(bppu_items) == 1
    assert bppu_items[0]["id"] == bppu1_id
    assert bppu_items[0]["siswa_name"] == siswa1.full_name

    # Filter BPPU by student username
    res_bppu_user = client.get(
        f"{BASE}/bppu",
        params={"q": siswa2.username.lower()},
        headers=guru_headers,
    )
    assert res_bppu_user.status_code == 200
    assert any(
        item["recipient_name"] == f"PT BPPU DUA {tag}"
        and item["siswa_name"] == siswa2.full_name
        for item in res_bppu_user.json()["items"]
    )

    # 2. Faktur Keluaran: create for siswa1 & siswa2
    fk_line = {
        "line_type": "barang",
        "item_code": "010000",
        "item_name": "Produk Uji",
        "unit": "Unit",
        "unit_price": 1_000_000,
        "quantity": 1,
        "discount": 0,
        "use_dpp_other": False,
        "ppn_rate_percent": 12,
        "ppnbm_rate_percent": 0,
    }
    fk1 = client.post(
        f"{BASE}/faktur-keluaran",
        json={
            "class_id": class1_id,
            "transaction_code": "01",
            "invoice_date": "2026-07-10",
            "tax_month": 7,
            "tax_year": 2026,
            "reference": f"REF-FK-{tag}",
            "buyer_identity_number": "0013457668062000",
            "buyer_name": f"PT BUYER FK {tag}",
            "lines": [fk_line],
        },
        headers=siswa1_headers,
    )
    assert fk1.status_code == 201, fk1.text
    fk1_id = fk1.json()["id"]

    # Search by reference
    res_fk_ref = client.get(
        f"{BASE}/faktur-keluaran", params={"q": f"REF-FK-{tag}"}, headers=guru_headers
    )
    assert res_fk_ref.status_code == 200
    fk_items = res_fk_ref.json()["items"]
    assert len(fk_items) == 1
    assert fk_items[0]["id"] == fk1_id
    assert fk_items[0]["siswa_name"] == siswa1.full_name

    # Search by buyer_name
    res_fk_buyer = client.get(
        f"{BASE}/faktur-keluaran", params={"q": f"buyer fk {tag.lower()}"}, headers=guru_headers
    )
    assert res_fk_buyer.status_code == 200
    assert [i["id"] for i in res_fk_buyer.json()["items"]] == [fk1_id]

    # Search by student full_name
    res_fk_student = client.get(
        f"{BASE}/faktur-keluaran", params={"q": siswa1.full_name}, headers=guru_headers
    )
    assert res_fk_student.status_code == 200
    assert any(
        item["id"] == fk1_id and item["siswa_name"] == siswa1.full_name
        for item in res_fk_student.json()["items"]
    )

    # 3. Faktur Masukan: create for siswa1
    fm1 = client.post(
        f"{BASE}/faktur-masukan",
        json={
            "class_id": class1_id,
            "transaction_code": "01",
            "invoice_date": "2026-07-11",
            "tax_month": 7,
            "tax_year": 2026,
            "reference": f"REF-FM-{tag}",
            "buyer_identity_number": "0013457668062000",
            "buyer_name": "PT Pembeli Simulasi",
            "seller_name": f"PT SELLER FM {tag}",
            "seller_npwp": "0023456789012000",
            "lines": [fk_line],
        },
        headers=siswa1_headers,
    )
    assert fm1.status_code == 201, fm1.text
    fm1_id = fm1.json()["id"]

    # Search by seller_name
    res_fm_seller = client.get(
        f"{BASE}/faktur-masukan", params={"q": f"SELLER FM {tag}"}, headers=guru_headers
    )
    assert res_fm_seller.status_code == 200
    fm_items = res_fm_seller.json()["items"]
    assert len(fm_items) == 1
    assert fm_items[0]["id"] == fm1_id
    assert fm_items[0]["siswa_name"] == siswa1.full_name

    # Search by student full_name
    res_fm_student = client.get(
        f"{BASE}/faktur-masukan", params={"q": siswa1.full_name}, headers=guru_headers
    )
    assert res_fm_student.status_code == 200
    assert any(
        item["id"] == fm1_id and item["siswa_name"] == siswa1.full_name
        for item in res_fm_student.json()["items"]
    )


def test_approve_auto_scores_bulk_grading_and_permissions(client: TestClient) -> None:
    """POST /tugas/{id}/approve-auto-scores approves ungraded auto-scores and syncs docs."""
    guru1_headers = auth_headers(client, GURU_1_A)
    guru2_headers = auth_headers(client, GURU_2_A)
    admin_headers = auth_headers(client, ADMIN_A)
    siswa1_headers = auth_headers(client, SISWA_1_A)
    siswa2_headers = auth_headers(client, SISWA_2_A)
    siswa1 = _get_user(SISWA_1_A)
    class1_id = _get_class_id(GURU_1_A)

    # Create and publish a BPPU assignment
    tugas_res = client.post(
        f"{BASE}/tugas",
        json={
            "class_id": class1_id,
            "title": "Tugas Approve Auto Score",
            "instructions": "Buat BPPU",
            "doc_type": "bppu",
            "publish": True,
        },
        headers=guru1_headers,
    )
    assert tugas_res.status_code == 201, tugas_res.text
    tugas_id = tugas_res.json()["id"]

    def _create_bppu(headers: dict[str, str], issue_doc: bool) -> int:
        r = client.post(
            f"{BASE}/bppu",
            json={
                "tax_month": 5,
                "tax_year": 2025,
                "recipient_identity_number": "0123456789012345",
                "recipient_name": "PT Mitra Bulk",
                "tax_object_code": "22-100-07",
                "tax_nature": "non_final",
                "tax_facility": "none",
                "dpp": 10_000_000,
                "rate_percent": 0.25,
                "document_type": "Faktur Pajak",
                "document_number": "FP-BULK",
                "document_date": "2025-05-15",
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        doc_id = r.json()["id"]
        if issue_doc:
            ir = client.post(
                f"{BASE}/bppu/{doc_id}/issue",
                json={"kode_otorisasi": "123456", "signer_name": "Siswa"},
                headers=headers,
            )
            assert ir.status_code == 200, ir.text
        return doc_id

    doc1_id = _create_bppu(siswa1_headers, issue_doc=True)
    doc2_id = _create_bppu(siswa2_headers, issue_doc=False)

    sub1 = client.post(
        f"{BASE}/tugas/{tugas_id}/submit",
        json={"doc_id": doc1_id},
        headers=siswa1_headers,
    )
    assert sub1.status_code == 200, sub1.text
    sub1_auto = sub1.json()["auto_score"]

    sub2 = client.post(
        f"{BASE}/tugas/{tugas_id}/submit",
        json={"doc_id": doc2_id},
        headers=siswa2_headers,
    )
    assert sub2.status_code == 200, sub2.text
    sub2_auto = sub2.json()["auto_score"]

    # Manually grade siswa1 first with a custom score (88)
    grade1 = client.patch(
        f"{BASE}/tugas/{tugas_id}/submissions/{siswa1.id}",
        json={"final_score": 88, "teacher_feedback": "Nilai manual"},
        headers=guru1_headers,
    )
    assert grade1.status_code == 200, grade1.text
    assert sub1_auto != 88

    # Non-owner guru (404) and admin/siswa (403) must be rejected
    assert (
        client.post(
            f"{BASE}/tugas/{tugas_id}/approve-auto-scores", headers=guru2_headers
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{BASE}/tugas/{tugas_id}/approve-auto-scores", headers=admin_headers
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"{BASE}/tugas/{tugas_id}/approve-auto-scores", headers=siswa1_headers
        ).status_code
        == 403
    )

    # Owner guru bulk-approves remaining ungraded auto-scores -> only siswa2 approved
    approve_res = client.post(
        f"{BASE}/tugas/{tugas_id}/approve-auto-scores", headers=guru1_headers
    )
    assert approve_res.status_code == 200, approve_res.text
    assert approve_res.json() == {"approved": 1}

    # Calling again approves 0
    approve_again = client.post(
        f"{BASE}/tugas/{tugas_id}/approve-auto-scores", headers=guru1_headers
    )
    assert approve_again.status_code == 200
    assert approve_again.json() == {"approved": 0}

    with Session(engine) as session:
        # Siswa 1 manual score 88 preserved
        slip1 = session.get(WithholdingSlip, doc1_id)
        assert slip1 is not None
        assert slip1.score == 88

        # Siswa 2 submission and underlying document synced to auto_score
        sub2_db = session.exec(
            select(AssignmentSubmission).where(
                AssignmentSubmission.assignment_id == tugas_id,
                AssignmentSubmission.doc_id == doc2_id,
            )
        ).one()
        assert sub2_db.final_score == sub2_auto
        assert sub2_db.graded_at is not None

        slip2 = session.get(WithholdingSlip, doc2_id)
        assert slip2 is not None
        assert slip2.score == sub2_auto

        # Audit log recorded (two calls: first approved 1, second approved 0)
        audits = session.exec(
            select(AuditLog)
            .where(
                AuditLog.action == "tugas.bulk_grade",
                AuditLog.entity_id == tugas_id,
            )
            .order_by(AuditLog.id.asc())
        ).all()
        assert len(audits) >= 1
        assert any("1 pengumpulan" in a.summary for a in audits)

        # Cleanup test assignment + submissions
        for row in session.exec(
            select(AssignmentSubmission).where(
                AssignmentSubmission.assignment_id == tugas_id
            )
        ).all():
            session.delete(row)
        asg = session.get(Assignment, tugas_id)
        if asg is not None:
            session.delete(asg)
        session.commit()
