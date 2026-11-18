"""Integration tests for Feature 1 (Tugas CRUD, RBAC, submission lifecycle, grading sync)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.models.assignment import Assignment, AssignmentSubmission
from app.models.school_class import SchoolClass
from app.models.slip import WithholdingSlip
from tests.conftest import auth_headers

SUPERADMIN = "super@pajaksim.local"
ADMIN_A = "admin@smkn1-pku.local"
GURU_1_A = "guru1@smkn1-pku.local"  # XII Akuntansi 1 (siswa 1, 2, 3)
GURU_2_A = "guru2@smkn1-pku.local"  # XII Akuntansi 2 (siswa 4, 5, 6)
SISWA_1_A = "siswa1@smkn1-pku.local"
SISWA_2_A = "siswa2@smkn1-pku.local"
SISWA_4_A = "siswa4@smkn1-pku.local"
GURU_1_B = "guru1@pcr.local"


def _get_class_id(guru_email: str) -> int:
    with Session(engine) as session:
        from app.models.user import User

        guru = session.exec(select(User).where(User.email == guru_email)).one()
        sc = session.exec(
            select(SchoolClass).where(SchoolClass.guru_id == guru.id)
        ).first()
        assert sc is not None
        return sc.id


def _create_issued_bppu(client: TestClient, siswa_headers: dict[str, str]) -> dict:
    res = client.post(
        "/api/v1/bppu",
        json={
            "tax_month": 5,
            "tax_year": 2025,
            "recipient_identity_number": "0123456789012345",
            "recipient_name": "PT Mitra Tugas",
            "tax_object_code": "22-100-07",
            "tax_nature": "non_final",
            "tax_facility": "none",
            "dpp": 10_000_000,
            "rate_percent": 0.25,
            "document_type": "Faktur Pajak",
            "document_number": "FP-001",
            "document_date": "2025-05-15",
        },
        headers=siswa_headers,
    )
    assert res.status_code == 201, res.text
    doc = res.json()
    issue = client.post(
        f"/api/v1/bppu/{doc['id']}/issue",
        json={"kode_otorisasi": "123456", "signer_name": "Siswa 1"},
        headers=siswa_headers,
    )
    assert issue.status_code == 200, issue.text
    return issue.json()


def test_tugas_lifecycle_and_grading_sync(client: TestClient):
    guru1_headers = auth_headers(client, GURU_1_A)
    guru2_headers = auth_headers(client, GURU_2_A)
    admin_headers = auth_headers(client, ADMIN_A)
    super_headers = auth_headers(client, SUPERADMIN)
    siswa1_headers = auth_headers(client, SISWA_1_A)
    siswa2_headers = auth_headers(client, SISWA_2_A)
    siswa4_headers = auth_headers(client, SISWA_4_A)

    class1_id = _get_class_id(GURU_1_A)
    class2_id = _get_class_id(GURU_2_A)

    # 1. Guru cannot create assignment for another guru's class
    res_wrong_class = client.post(
        "/api/v1/tugas",
        json={
            "class_id": class2_id,
            "title": "Tugas Salah Kelas",
            "instructions": "Instruksi",
            "doc_type": "bppu",
        },
        headers=guru1_headers,
    )
    assert res_wrong_class.status_code == 404

    # 2. Guru creates draft assignment
    res_create = client.post(
        "/api/v1/tugas",
        json={
            "class_id": class1_id,
            "title": "Latihan BPPU Pasal 22 Semen",
            "instructions": "Buat dan terbitkan BPPU atas penjualan semen Rp10.000.000.",
            "doc_type": "bppu",
            "publish": False,
        },
        headers=guru1_headers,
    )
    assert res_create.status_code == 201, res_create.text
    tugas = res_create.json()
    tugas_id = tugas["id"]
    assert tugas["status"] == "draft"
    assert tugas["enrolled_count"] >= 3

    # 3. Draft assignment is invisible to enrolled siswa
    res_siswa_draft = client.get(f"/api/v1/tugas/{tugas_id}", headers=siswa1_headers)
    assert res_siswa_draft.status_code == 404

    # 4. Guru updates assignment and publishes
    future_due = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    res_upd = client.patch(
        f"/api/v1/tugas/{tugas_id}",
        json={"title": "Latihan BPPU Pasal 22 (Revisi)", "due_at": future_due},
        headers=guru1_headers,
    )
    assert res_upd.status_code == 200
    assert res_upd.json()["title"] == "Latihan BPPU Pasal 22 (Revisi)"

    res_pub = client.post(f"/api/v1/tugas/{tugas_id}/publish", headers=guru1_headers)
    assert res_pub.status_code == 200
    assert res_pub.json()["status"] == "published"

    # 5. Enrolled siswa sees assignment in list and in dashboard todo
    res_list_s1 = client.get("/api/v1/tugas", headers=siswa1_headers)
    assert res_list_s1.status_code == 200
    assert any(a["id"] == tugas_id for a in res_list_s1.json())

    # Siswa from class 2 cannot see class 1 assignment
    res_s4 = client.get(f"/api/v1/tugas/{tugas_id}", headers=siswa4_headers)
    assert res_s4.status_code == 404

    # Check siswa1 dashboard has tugas todo
    dash_s1 = client.get("/api/v1/dashboard/siswa", headers=siswa1_headers).json()
    assert dash_s1["open_tugas_count"] >= 1
    assert any(
        t["kind"] == "tugas" and t["id"] == tugas_id for t in dash_s1["todos"]
    )

    # 6. Siswa 1 creates a draft BPPU and submits -> gets partial score (70)
    draft_bppu = client.post(
        "/api/v1/bppu",
        json={
            "tax_month": 5,
            "tax_year": 2025,
            "recipient_identity_number": "0123456789012345",
            "recipient_name": "PT Mitra Draft",
            "tax_object_code": "22-100-07",
            "tax_nature": "non_final",
            "tax_facility": "none",
            "dpp": 10_000_000,
            "rate_percent": 0.25,
            "document_date": "2025-05-10",
        },
        headers=siswa1_headers,
    ).json()

    # Candidate listing returns student's own BPPU documents
    cands = client.get(
        f"/api/v1/tugas/{tugas_id}/candidates", headers=siswa1_headers
    )
    assert cands.status_code == 200
    assert any(c["id"] == draft_bppu["id"] for c in cands.json())

    sub1 = client.post(
        f"/api/v1/tugas/{tugas_id}/submit",
        json={"doc_id": draft_bppu["id"]},
        headers=siswa1_headers,
    )
    assert sub1.status_code == 200, sub1.text
    sub1_data = sub1.json()
    assert sub1_data["auto_score"] == 70
    assert sub1_data["is_late"] is False

    # 7. Siswa 2 cannot submit Siswa 1's document
    wrong_owner = client.post(
        f"/api/v1/tugas/{tugas_id}/submit",
        json={"doc_id": draft_bppu["id"]},
        headers=siswa2_headers,
    )
    assert wrong_owner.status_code == 404

    # 8. Siswa 1 cannot submit a wrong doc_type (e.g. BP21 id)
    bp21_doc = client.post(
        "/api/v1/bp21",
        json={
            "tax_month": 5,
            "tax_year": 2025,
            "recipient_identity_number": "3201234567890001",
            "recipient_name": "Pegawai",
            "tax_object_code": "21-100-04",
            "tax_nature": "non_final",
            "tax_facility": "none",
            "ter_category": "A",
            "ptkp_status": "TK/0",
            "gross_income": 8_000_000,
            "dpp_percent": 100,
            "rate_percent": 1.25,
        },
        headers=siswa1_headers,
    ).json()
    wrong_type = client.post(
        f"/api/v1/tugas/{tugas_id}/submit",
        json={"doc_id": bp21_doc["id"]},
        headers=siswa1_headers,
    )
    assert wrong_type.status_code == 404

    # 9. Siswa 1 issues a valid BPPU and resubmits before grading -> auto_score becomes 100
    issued_bppu = _create_issued_bppu(client, siswa1_headers)
    sub2 = client.post(
        f"/api/v1/tugas/{tugas_id}/submit",
        json={"doc_id": issued_bppu["id"]},
        headers=siswa1_headers,
    )
    assert sub2.status_code == 200
    assert sub2.json()["auto_score"] == 100
    assert sub2.json()["displayed_score"] == 100

    # 10. Guru views submissions list -> includes all enrolled siswa, with "belum mengumpulkan" rows
    subs_res = client.get(
        f"/api/v1/tugas/{tugas_id}/submissions", headers=guru1_headers
    )
    assert subs_res.status_code == 200
    subs_list = subs_res.json()
    assert len(subs_list) >= 3
    s1_row = next(r for r in subs_list if r["siswa_email"] == SISWA_1_A)
    s2_row = next(r for r in subs_list if r["siswa_email"] == SISWA_2_A)
    assert s1_row["submitted"] is True
    assert s1_row["auto_score"] == 100
    assert s2_row["submitted"] is False
    assert s2_row["doc_id"] is None

    # Guru dashboard shows tugas_pending_grading >= 1
    dash_g1 = client.get(
        f"/api/v1/dashboard/guru?class_id={class1_id}", headers=guru1_headers
    ).json()
    assert dash_g1["tugas_pending_grading"] >= 1

    # 11. Cannot delete assignment once it has a submission
    del_conflict = client.delete(
        f"/api/v1/tugas/{tugas_id}", headers=guru1_headers
    )
    assert del_conflict.status_code == 409

    # 12. Guru grades Siswa 1's submission -> score synced to WithholdingSlip
    grade_res = client.patch(
        f"/api/v1/tugas/{tugas_id}/submissions/{s1_row['siswa_id']}",
        json={
            "final_score": 95,
            "teacher_feedback": "Sangat baik, nomor faktur referensi sudah tepat.",
        },
        headers=guru1_headers,
    )
    assert grade_res.status_code == 200, grade_res.text
    graded = grade_res.json()
    assert graded["final_score"] == 95
    assert graded["displayed_score"] == 95
    assert (
        graded["teacher_feedback"]
        == "Sangat baik, nomor faktur referensi sudah tepat."
    )

    with Session(engine) as session:
        slip_db = session.get(WithholdingSlip, issued_bppu["id"])
        assert slip_db is not None
        assert slip_db.score == 95
        assert (
            slip_db.teacher_feedback
            == "Sangat baik, nomor faktur referensi sudah tepat."
        )

    # 13. Siswa 1 cannot resubmit after guru has graded
    resubmit_blocked = client.post(
        f"/api/v1/tugas/{tugas_id}/submit",
        json={"doc_id": issued_bppu["id"]},
        headers=siswa1_headers,
    )
    assert resubmit_blocked.status_code == 409

    # 14. Admin & superadmin read access; guru2 blocked
    assert (
        client.get(f"/api/v1/tugas/{tugas_id}", headers=admin_headers).status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/tugas/{tugas_id}", headers=super_headers).status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/tugas/{tugas_id}", headers=guru2_headers).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/tugas/{tugas_id}", headers=auth_headers(client, GURU_1_B)
        ).status_code
        == 404
    )


def test_late_submission_and_closed_rejection_and_delete_without_submissions(
    client: TestClient,
):
    guru1_headers = auth_headers(client, GURU_1_A)
    siswa1_headers = auth_headers(client, SISWA_1_A)
    class1_id = _get_class_id(GURU_1_A)

    # Past due_at -> submission accepted with is_late=True
    past_due = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    tugas = client.post(
        "/api/v1/tugas",
        json={
            "class_id": class1_id,
            "title": "Tugas Tenggat Lewat",
            "instructions": "Kumpulkan BPPU",
            "doc_type": "bppu",
            "due_at": past_due,
            "publish": True,
        },
        headers=guru1_headers,
    ).json()

    issued_bppu = _create_issued_bppu(client, siswa1_headers)
    sub = client.post(
        f"/api/v1/tugas/{tugas['id']}/submit",
        json={"doc_id": issued_bppu["id"]},
        headers=siswa1_headers,
    )
    assert sub.status_code == 200
    assert sub.json()["is_late"] is True

    # Close assignment -> further submissions rejected
    close_res = client.post(
        f"/api/v1/tugas/{tugas['id']}/close", headers=guru1_headers
    )
    assert close_res.status_code == 200
    assert close_res.json()["status"] == "closed"

    sub_after_close = client.post(
        f"/api/v1/tugas/{tugas['id']}/submit",
        json={"doc_id": issued_bppu["id"]},
        headers=siswa1_headers,
    )
    assert sub_after_close.status_code == 409

    # Create an assignment with no submissions and delete it -> 204
    empty_tugas = client.post(
        "/api/v1/tugas",
        json={
            "class_id": class1_id,
            "title": "Tugas Kosong Untuk Dihapus",
            "instructions": "Tes hapus",
            "doc_type": "bppu",
        },
        headers=guru1_headers,
    ).json()
    del_res = client.delete(
        f"/api/v1/tugas/{empty_tugas['id']}", headers=guru1_headers
    )
    assert del_res.status_code == 204

    # Cleanup test assignments created
    with Session(engine) as session:
        for row in session.exec(select(AssignmentSubmission)).all():
            session.delete(row)
        for row in session.exec(select(Assignment)).all():
            session.delete(row)
        session.commit()
