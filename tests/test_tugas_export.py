"""Integration tests for Feature 3 (Export nilai per tugas and rekap kelas in Excel & CSV)."""

from __future__ import annotations

import csv
import io

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlmodel import Session, select

from app.db import engine
from app.models.assignment import Assignment, AssignmentSubmission
from tests.conftest import auth_headers
from tests.test_tugas import (
    ADMIN_A,
    GURU_1_A,
    GURU_2_A,
    SISWA_1_A,
    _create_issued_bppu,
    _get_class_id,
)


def test_export_assignment_and_class_rekap_xlsx_and_csv(client: TestClient):
    guru1_headers = auth_headers(client, GURU_1_A)
    guru2_headers = auth_headers(client, GURU_2_A)
    admin_headers = auth_headers(client, ADMIN_A)
    siswa1_headers = auth_headers(client, SISWA_1_A)
    class1_id = _get_class_id(GURU_1_A)

    # Create and publish 2 assignments in class 1
    t1 = client.post(
        "/api/v1/tugas",
        json={
            "class_id": class1_id,
            "title": "Tugas 1 BPPU",
            "instructions": "Instruksi 1",
            "doc_type": "bppu",
            "publish": True,
        },
        headers=guru1_headers,
    ).json()
    t2 = client.post(
        "/api/v1/tugas",
        json={
            "class_id": class1_id,
            "title": "Tugas 2 BPPU",
            "instructions": "Instruksi 2",
            "doc_type": "bppu",
            "publish": True,
        },
        headers=guru1_headers,
    ).json()
    assert t2["id"] != t1["id"]

    # Siswa 1 submits to Tugas 1 and gets graded 92
    doc = _create_issued_bppu(client, siswa1_headers)
    sub_res = client.post(
        f"/api/v1/tugas/{t1['id']}/submit",
        json={"doc_id": doc["id"]},
        headers=siswa1_headers,
    )
    assert sub_res.status_code == 200
    s1_id = sub_res.json()["siswa_id"]

    client.patch(
        f"/api/v1/tugas/{t1['id']}/submissions/{s1_id}",
        json={"final_score": 92, "teacher_feedback": "Bagus sekali"},
        headers=guru1_headers,
    )

    # 1. Per-assignment XLSX export
    xlsx_res = client.get(
        f"/api/v1/tugas/{t1['id']}/export?format=xlsx", headers=guru1_headers
    )
    assert xlsx_res.status_code == 200
    assert (
        xlsx_res.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    wb = load_workbook(io.BytesIO(xlsx_res.content))
    ws = wb.active
    assert ws.page_setup.orientation == "landscape"
    assert ws.freeze_panes == "A2"
    rows = list(ws.iter_values() if hasattr(ws, "iter_values") else ws.values)
    assert rows[0] == (
        "Nama",
        "NIS",
        "Status",
        "Waktu kumpul",
        "Terlambat",
        "Nilai otomatis",
        "Nilai akhir",
        "Umpan balik",
    )
    s1_excel_row = next(r for r in rows[1:] if "Siswa 1" in str(r[0]))
    assert s1_excel_row[2] == "Sudah dinilai"
    assert s1_excel_row[5] == 100
    assert s1_excel_row[6] == 92
    assert s1_excel_row[7] == "Bagus sekali"

    unsubmitted_row = next(r for r in rows[1:] if "Siswa 2" in str(r[0]))
    assert unsubmitted_row[2] == "Belum mengumpulkan"

    # 2. Per-assignment CSV export
    csv_res = client.get(
        f"/api/v1/tugas/{t1['id']}/export?format=csv", headers=admin_headers
    )
    assert csv_res.status_code == 200
    assert "text/csv" in csv_res.headers["content-type"]
    csv_rows = list(
        csv.reader(io.StringIO(csv_res.content.decode("utf-8-sig")))
    )
    assert csv_rows[0] == [
        "Nama",
        "NIS",
        "Status",
        "Waktu kumpul",
        "Terlambat",
        "Nilai otomatis",
        "Nilai akhir",
        "Umpan balik",
    ]
    assert len(csv_rows) >= 4

    # 3. Class rekap XLSX & CSV export (accessible by guru1 and admin, forbidden to guru2 and siswa)
    rekap_xlsx = client.get(
        f"/api/v1/tugas/rekap-kelas/{class1_id}/export?format=xlsx",
        headers=admin_headers,
    )
    assert rekap_xlsx.status_code == 200
    wb_rekap = load_workbook(io.BytesIO(rekap_xlsx.content))
    rekap_rows = list(wb_rekap.active.values)
    assert rekap_rows[0][0] == "Nama"
    assert rekap_rows[0][1] == "NIS"
    assert rekap_rows[0][-1] == "Rata-rata"
    s1_rekap = next(r for r in rekap_rows[1:] if "Siswa 1" in str(r[0]))
    # Siswa 1 has 92 on Tugas 1 and None (0) on Tugas 2 -> average 46.0
    assert 92 in s1_rekap
    assert s1_rekap[-1] == 46

    rekap_csv = client.get(
        f"/api/v1/tugas/rekap-kelas/{class1_id}/export?format=csv",
        headers=guru1_headers,
    )
    assert rekap_csv.status_code == 200

    assert (
        client.get(
            f"/api/v1/tugas/rekap-kelas/{class1_id}/export?format=xlsx",
            headers=guru2_headers,
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/tugas/rekap-kelas/{class1_id}/export?format=xlsx",
            headers=siswa1_headers,
        ).status_code
        == 403
    )

    # Cleanup
    with Session(engine) as session:
        for row in session.exec(select(AssignmentSubmission)).all():
            session.delete(row)
        for row in session.exec(select(Assignment)).all():
            session.delete(row)
        session.commit()
