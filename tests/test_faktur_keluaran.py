from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session as ModelSession
from sqlmodel import select

from app.db import engine
from app.models.school_class import SchoolClass
from app.models.user import User
from tests.conftest import auth_headers

SISWA = "siswa1@smkn1-pku.local"
OTHER_SISWA = "siswa4@smkn1-pku.local"
GURU = "guru1@smkn1-pku.local"
ADMIN = "admin@smkn1-pku.local"
SUPERADMIN = "super@pajaksim.local"
PCR_ADMIN = "admin@pcr.local"


def _line(**overrides) -> dict:
    line = {
        "line_type": "jasa",
        "item_code": "060000",
        "item_name": "Biaya Kirim Barang",
        "unit": "Bulan",
        "unit_price": 1_000_000,
        "quantity": 1,
        "discount": 0,
        "use_dpp_other": False,
        "ppn_rate_percent": 12,
        "ppnbm_rate_percent": 0,
    }
    line.update(overrides)
    return line


def _payload(**overrides) -> dict:
    payload = {
        "transaction_code": "01",
        "invoice_date": "2026-07-18",
        "tax_month": 7,
        "tax_year": 2026,
        "buyer_identity_number": "0013457668062000",
        "buyer_name": "PT Pembeli Simulasi",
        "buyer_address": "JL POS PENGUMBEN RAYA NO.8",
        "buyer_idtku": "000000",
        "lines": [_line()],
    }
    payload.update(overrides)
    return payload


def _create(client: TestClient, headers: dict, **overrides) -> dict:
    res = client.post("/api/v1/faktur-keluaran", json=_payload(**overrides), headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def test_create_normal_code_uses_full_dpp(client: TestClient):
    headers = auth_headers(client, SISWA)
    data = _create(client, headers)

    assert data["status"] == "draft"
    assert data["invoice_number"] is None
    assert data["total_price"] == 1_000_000
    assert data["total_dpp"] == 1_000_000
    assert data["total_dpp_other"] == 0
    assert data["total_ppn"] == 120_000
    assert len(data["lines"]) == 1
    assert data["lines"][0]["quantity"] == 1


def test_transaction_code_05_forces_dpp_nilai_lain(client: TestClient):
    """Kode 05 (besaran tertentu) always bases PPN on 11/12 of the price."""
    headers = auth_headers(client, SISWA)
    data = _create(client, headers, transaction_code="05")

    assert data["total_dpp"] == 1_000_000
    assert data["total_dpp_other"] == 916_667  # 1_000_000 * 11/12
    assert data["total_ppn"] == 110_000  # 12% of 916_667
    assert data["lines"][0]["use_dpp_other"] is True


def test_transaction_code_04_forces_dpp_nilai_lain_even_if_unchecked(client: TestClient):
    headers = auth_headers(client, SISWA)
    data = _create(
        client, headers, transaction_code="04", lines=[_line(use_dpp_other=False)]
    )

    assert data["total_dpp_other"] == 916_667
    assert data["total_ppn"] == 110_000


def test_discount_reduces_dpp(client: TestClient):
    headers = auth_headers(client, SISWA)
    data = _create(client, headers, lines=[_line(discount=200_000)])

    assert data["total_price"] == 1_000_000
    assert data["total_discount"] == 200_000
    assert data["total_dpp"] == 800_000
    assert data["total_ppn"] == 96_000


def test_fractional_quantity_and_multi_line_totals(client: TestClient):
    headers = auth_headers(client, SISWA)
    data = _create(
        client,
        headers,
        lines=[
            _line(unit_price=1_000_000, quantity=2.5),
            _line(unit_price=250_000, quantity=3, item_name="Jasa Bongkar Muat"),
        ],
    )

    assert data["lines"][0]["total_price"] == 2_500_000
    assert data["lines"][1]["total_price"] == 750_000
    assert data["total_price"] == 3_250_000
    assert data["total_dpp"] == 3_250_000
    assert data["total_ppn"] == 390_000
    assert data["total_ppn"] == sum(line["ppn"] for line in data["lines"])


def test_ppnbm_is_calculated_from_dpp(client: TestClient):
    headers = auth_headers(client, SISWA)
    data = _create(client, headers, lines=[_line(ppnbm_rate_percent=20)])

    assert data["total_ppnbm"] == 200_000
    assert data["lines"][0]["ppnbm_rate_percent"] == 20


def test_issue_assigns_16_digit_invoice_number(client: TestClient):
    headers = auth_headers(client, SISWA)
    created = _create(client, headers, transaction_code="05")

    res = client.post(f"/api/v1/faktur-keluaran/{created['id']}/issue", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()

    number = data["invoice_number"]
    assert data["status"] == "issued"
    assert number is not None
    assert len(number) == 16 and number.isdigit()
    assert number.startswith("05")  # kode transaksi
    assert number[2] == "0"  # 0 = faktur normal
    assert number[6:8] == "26"  # tax_year 2026

    # A second issue is a conflict, not a silent re-number.
    again = client.post(f"/api/v1/faktur-keluaran/{created['id']}/issue", headers=headers)
    assert again.status_code == 409


def test_issue_rejects_invoice_without_lines(client: TestClient):
    headers = auth_headers(client, SISWA)
    created = _create(client, headers, lines=[])

    res = client.post(f"/api/v1/faktur-keluaran/{created['id']}/issue", headers=headers)
    assert res.status_code == 409


def test_delete_only_allowed_while_draft(client: TestClient):
    headers = auth_headers(client, SISWA)

    draft = _create(client, headers)
    removed = client.delete(f"/api/v1/faktur-keluaran/{draft['id']}", headers=headers)
    assert removed.status_code == 204

    issued = _create(client, headers)
    client.post(f"/api/v1/faktur-keluaran/{issued['id']}/issue", headers=headers)
    denied = client.delete(f"/api/v1/faktur-keluaran/{issued['id']}", headers=headers)
    assert denied.status_code == 409


def test_cancel_marks_issued_invoice_invalid(client: TestClient):
    """Only admin/superadmin may cancel; the issued faktur becomes Tidak Valid."""
    siswa_headers = auth_headers(client, SISWA)
    created = _create(client, siswa_headers)
    client.post(f"/api/v1/faktur-keluaran/{created['id']}/issue", headers=siswa_headers)

    for role_headers in (auth_headers(client, SISWA), auth_headers(client, GURU)):
        denied = client.post(
            f"/api/v1/faktur-keluaran/{created['id']}/cancel",
            json={"reason": "Salah input pembeli"},
            headers=role_headers,
        )
        assert denied.status_code == 403

    res = client.post(
        f"/api/v1/faktur-keluaran/{created['id']}/cancel",
        json={"reason": "Salah input pembeli"},
        headers=auth_headers(client, ADMIN),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "invalid"
    assert data["invoice_kind"] == "dibatalkan"
    assert data["invalid_reason"] == "Salah input pembeli"


def test_cancel_role_matrix(client: TestClient):
    """siswa and guru may never cancel; admin and superadmin may."""
    base_headers = auth_headers(client, SISWA)

    def _issued():
        created = _create(client, base_headers)
        client.post(f"/api/v1/faktur-keluaran/{created['id']}/issue", headers=base_headers)
        return created["id"]

    for email in (SISWA, GURU):
        res = client.post(
            f"/api/v1/faktur-keluaran/{_issued()}/cancel",
            json={"reason": "x"},
            headers=auth_headers(client, email),
        )
        assert res.status_code == 403

    for email in (ADMIN, SUPERADMIN):
        res = client.post(
            f"/api/v1/faktur-keluaran/{_issued()}/cancel",
            json={"reason": "x"},
            headers=auth_headers(client, email),
        )
        assert res.status_code == 200


def test_lines_can_be_added_and_removed_with_totals_kept_in_sync(client: TestClient):
    headers = auth_headers(client, SISWA)
    created = _create(client, headers, lines=[])
    invoice_id = created["id"]
    assert created["total_ppn"] == 0

    added = client.post(
        f"/api/v1/faktur-keluaran/{invoice_id}/lines", json=_line(), headers=headers
    )
    assert added.status_code == 201
    assert added.json()["total_ppn"] == 120_000

    line_id = added.json()["lines"][0]["id"]
    patched = client.patch(
        f"/api/v1/faktur-keluaran/{invoice_id}/lines/{line_id}",
        json={"quantity": 2},
        headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["total_ppn"] == 240_000

    removed = client.delete(
        f"/api/v1/faktur-keluaran/{invoice_id}/lines/{line_id}", headers=headers
    )
    assert removed.status_code == 200
    assert removed.json()["total_ppn"] == 0


def test_patch_replacing_lines_recomputes_totals(client: TestClient):
    headers = auth_headers(client, SISWA)
    created = _create(client, headers)

    res = client.patch(
        f"/api/v1/faktur-keluaran/{created['id']}",
        json={"lines": [_line(unit_price=500_000)]},
        headers=headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data["lines"]) == 1
    assert data["total_dpp"] == 500_000
    assert data["total_ppn"] == 60_000


def test_siswa_cannot_read_another_siswa_invoice(client: TestClient):
    owner_headers = auth_headers(client, SISWA)
    created = _create(client, owner_headers)

    intruder_headers = auth_headers(client, OTHER_SISWA)
    res = client.get(f"/api/v1/faktur-keluaran/{created['id']}", headers=intruder_headers)
    assert res.status_code == 404


def test_summary_counts_only_own_invoices(client: TestClient):
    headers = auth_headers(client, SISWA)
    before = client.get("/api/v1/faktur-keluaran/summary", headers=headers).json()
    _create(client, headers)
    after = client.get("/api/v1/faktur-keluaran/summary", headers=headers).json()

    assert after["draft"] == before["draft"] + 1
    assert after["total"] == before["total"] + 1


def test_guru_can_review_but_siswa_cannot(client: TestClient):
    siswa_headers = auth_headers(client, SISWA)
    classes = client.get("/api/v1/classes", headers=siswa_headers).json()
    created = _create(client, siswa_headers, class_id=classes[0]["id"])

    guru_headers = auth_headers(client, GURU)
    res = client.patch(
        f"/api/v1/faktur-keluaran/{created['id']}/review",
        json={"score": 90, "teacher_feedback": "Kode transaksi sudah tepat"},
        headers=guru_headers,
    )
    assert res.status_code == 200
    assert res.json()["score"] == 90

    denied = client.patch(
        f"/api/v1/faktur-keluaran/{created['id']}/review",
        json={"score": 100},
        headers=siswa_headers,
    )
    assert denied.status_code == 403


def test_list_filters_by_transaction_code(client: TestClient):
    headers = auth_headers(client, SISWA)
    _create(client, headers, transaction_code="07")

    res = client.get("/api/v1/faktur-keluaran?transaction_code=07", headers=headers)
    assert res.status_code == 200
    items = res.json()["items"]
    assert items
    assert all(item["transaction_code"] == "07" for item in items)


def test_invalid_transaction_code_is_rejected(client: TestClient):
    headers = auth_headers(client, SISWA)
    res = client.post(
        "/api/v1/faktur-keluaran", json=_payload(transaction_code="99"), headers=headers
    )
    assert res.status_code == 422


# --- P0 fixes ---------------------------------------------------------------


def test_tax_period_mismatch_is_422(client: TestClient):
    """Tanggal Faktur must land in the chosen Masa Pajak / Tahun Pajak."""
    headers = auth_headers(client, SISWA)
    res = client.post(
        "/api/v1/faktur-keluaran",
        json=_payload(invoice_date="2026-07-18", tax_month=8, tax_year=2026),
        headers=headers,
    )
    assert res.status_code == 422

    ok = client.post(
        "/api/v1/faktur-keluaran",
        json=_payload(invoice_date="2026-07-18", tax_month=7, tax_year=2026),
        headers=headers,
    )
    assert ok.status_code == 201


def test_pengganti_kind_is_rejected(client: TestClient):
    """Jenis faktur pengganti is disabled — create must reject it."""
    headers = auth_headers(client, SISWA)
    res = client.post(
        "/api/v1/faktur-keluaran", json=_payload(invoice_kind="pengganti"), headers=headers
    )
    assert res.status_code == 422


def test_issue_serials_are_unique_per_tenant_year(client: TestClient):
    """Two issues in the same tenant + year get distinct 16-digit numbers."""
    headers = auth_headers(client, SISWA)
    first = _create(client, headers)
    second = _create(client, headers)

    n1 = client.post(
        f"/api/v1/faktur-keluaran/{first['id']}/issue", headers=headers
    ).json()["invoice_number"]
    n2 = client.post(
        f"/api/v1/faktur-keluaran/{second['id']}/issue", headers=headers
    ).json()["invoice_number"]

    assert n1 != n2
    assert len(n1) == 16 and n1.isdigit()
    assert len(n2) == 16 and n2.isdigit()


def test_inaccessible_mutations_return_404(client: TestClient):
    """Reads/writes on a faktur owned by another tenant user are hidden (404)."""
    owner = auth_headers(client, SISWA)
    created = _create(client, owner)

    intruder = auth_headers(client, OTHER_SISWA)
    # Reads/writes on another student's faktur are hidden (404 via _get_accessible_invoice).
    patch = client.patch(
        f"/api/v1/faktur-keluaran/{created['id']}", json={"buyer_name": "Hack"}, headers=intruder
    )
    assert patch.status_code == 404

    delete = client.delete(f"/api/v1/faktur-keluaran/{created['id']}", headers=intruder)
    assert delete.status_code == 404

    issue = client.post(f"/api/v1/faktur-keluaran/{created['id']}/issue", headers=intruder)
    assert issue.status_code == 404

    # cancel is role-gated (admin/superadmin), so a siswa intruder is refused 403.
    res = client.post(
        f"/api/v1/faktur-keluaran/{created['id']}/cancel", json={"reason": "x"}, headers=intruder
    )
    assert res.status_code == 403


def test_bulk_issue_partial_success(client: TestClient):
    """Invalid items are reported while the rest still get issued."""
    headers = auth_headers(client, SISWA)
    valid = _create(client, headers)
    already = _create(client, headers)
    no_lines = _create(client, headers, lines=[])

    client.post(f"/api/v1/faktur-keluaran/{already['id']}/issue", headers=headers)

    other_headers = auth_headers(client, OTHER_SISWA)
    other = _create(client, other_headers)

    res = client.post(
        "/api/v1/faktur-keluaran/bulk-issue",
        json={"ids": [valid["id"], already["id"], other["id"], no_lines["id"], 999_999]},
        headers=headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["issued"] == 1
    assert data["skipped"] == 4
    assert len(data["errors"]) == 4

    state = client.get(f"/api/v1/faktur-keluaran/{valid['id']}", headers=headers).json()
    assert state["status"] == "issued"


# --- P1 fixes ---------------------------------------------------------------


def test_quantity_accepts_up_to_3_decimals(client: TestClient):
    headers = auth_headers(client, SISWA)
    ok = _create(client, headers, lines=[_line(quantity=1.234)])
    assert ok["lines"][0]["quantity"] == 1.234

    ok001 = _create(client, headers, lines=[_line(quantity=0.001)])
    assert ok001["lines"][0]["quantity"] == 0.001


def test_quantity_rejects_more_than_3_decimals(client: TestClient):
    headers = auth_headers(client, SISWA)
    res = client.post(
        "/api/v1/faktur-keluaran",
        json=_payload(lines=[_line(quantity=1.2345)]),
        headers=headers,
    )
    assert res.status_code == 422


def test_buyer_identity_validation(client: TestClient):
    headers = auth_headers(client, SISWA)

    # NIK must be exactly 16 digits.
    res = client.post(
        "/api/v1/faktur-keluaran",
        json=_payload(buyer_identity_type="nik", buyer_identity_number="123456789012345"),
        headers=headers,
    )
    assert res.status_code == 422

    ok = client.post(
        "/api/v1/faktur-keluaran",
        json=_payload(buyer_identity_type="nik", buyer_identity_number="1234567890123456"),
        headers=headers,
    )
    assert ok.status_code == 201

    # NPWP must be 15-16 numeric digits.
    res = client.post(
        "/api/v1/faktur-keluaran",
        json=_payload(buyer_identity_type="npwp", buyer_identity_number="not-a-npwp"),
        headers=headers,
    )
    assert res.status_code == 422

    # Paspor / Identitas Lain need both an identity number and a document number.
    res = client.post(
        "/api/v1/faktur-keluaran",
        json=_payload(
            buyer_identity_type="paspor",
            buyer_identity_number="P123456",
            buyer_document_number=None,
        ),
        headers=headers,
    )
    assert res.status_code == 422

    ok = client.post(
        "/api/v1/faktur-keluaran",
        json=_payload(
            buyer_identity_type="paspor",
            buyer_identity_number="P123456",
            buyer_document_number="DOC-001",
        ),
        headers=headers,
    )
    assert ok.status_code == 201


def test_export_matches_list_filters_and_does_not_leak(client: TestClient):
    """Export uses the same filter builder as the list and stays tenant-scoped."""
    headers = auth_headers(client, SISWA)
    _create(client, headers, transaction_code="07")

    other_headers = auth_headers(client, OTHER_SISWA)
    _create(client, other_headers, transaction_code="07", buyer_name="PT RAHASIA LAIN")

    res = client.get(
        "/api/v1/faktur-keluaran/export-csv?transaction_code=07", headers=headers
    )
    assert res.status_code == 200
    # The other student's unique buyer name must not leak into this export.
    assert "PT RAHASIA LAIN" not in res.text




def test_admin_cannot_read_other_tenant_invoice(client: TestClient):
    """An admin of a different tenant is hidden from another tenant's faktur."""
    headers = auth_headers(client, SISWA)
    created = _create(client, headers)

    res = client.get(
        f"/api/v1/faktur-keluaran/{created['id']}", headers=auth_headers(client, PCR_ADMIN)
    )
    assert res.status_code == 404


def test_guru_cannot_move_invoice_to_class_not_owned(client: TestClient):
    """A guru may only move a faktur between classes they teach."""
    siswa_headers = auth_headers(client, SISWA)
    my_class = client.get("/api/v1/classes", headers=siswa_headers).json()[0]["id"]
    created = _create(client, siswa_headers, class_id=my_class)

    guru_headers = auth_headers(client, GURU)
    guru_id = client.get("/api/v1/auth/me", headers=guru_headers).json()["id"]

    with ModelSession(engine) as session:
        other_class = session.exec(
            select(SchoolClass).where(
                SchoolClass.tenant_id == created["tenant_id"],
                SchoolClass.guru_id != guru_id,
            )
        ).first()
    assert other_class is not None

    res = client.patch(
        f"/api/v1/faktur-keluaran/{created['id']}",
        json={"class_id": other_class.id},
        headers=guru_headers,
    )
    assert res.status_code == 403

def test_deleting_user_with_invoices_is_rejected(client: TestClient):
    """created_by_id uses ON DELETE RESTRICT, so invoicing history is preserved."""
    headers = auth_headers(client, SISWA)
    created = _create(client, headers)
    siswa_id = created["siswa_id"]

    with ModelSession(engine) as session:
        user = session.get(User, siswa_id)
        blocked = False
        try:
            session.delete(user)
            session.commit()
        except IntegrityError:
            session.rollback()
            blocked = True

    assert blocked