from fastapi.testclient import TestClient

from tests.conftest import auth_headers
from tests.test_faktur_keluaran import _line as keluaran_line
from tests.test_faktur_keluaran import _payload as keluaran_payload

SISWA = "siswa1@smkn1-pku.local"
OTHER_SISWA = "siswa4@smkn1-pku.local"
GURU = "guru1@smkn1-pku.local"
ADMIN = "admin@smkn1-pku.local"
SUPERADMIN = "super@pajaksim.local"
PCR_ADMIN = "admin@pcr.local"


def _line(**overrides) -> dict:
    line = {
        "line_type": "barang",
        "item_code": "010000",
        "item_name": "Kertas HVS A4",
        "unit": "Paket",
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
        "seller_name": "PT Penjual Simulasi",
        "seller_npwp": "0023456789012000",
        "lines": [_line()],
    }
    payload.update(overrides)
    return payload


def _create(client: TestClient, headers: dict, **overrides) -> dict:
    res = client.post("/api/v1/faktur-masukan", json=_payload(**overrides), headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def test_create_normal_code_uses_full_dpp(client: TestClient):
    headers = auth_headers(client, SISWA)
    data = _create(client, headers)

    assert data["status"] == "draft"
    assert data["credit_status"] == "not_credited"
    assert data["total_dpp"] == 1_000_000
    assert data["total_dpp_other"] == 0
    assert data["total_ppn"] == 120_000


def test_transaction_code_04_forces_dpp_nilai_lain(client: TestClient):
    headers = auth_headers(client, SISWA)
    data = _create(client, headers, transaction_code="04")

    assert data["total_dpp"] == 1_000_000
    assert data["total_dpp_other"] == 916_667
    assert data["total_ppn"] == 110_000
    assert data["lines"][0]["use_dpp_other"] is True


def test_credit_changes_status_and_credited_at(client: TestClient):
    headers = auth_headers(client, SISWA)
    created = _create(client, headers)

    res = client.post(f"/api/v1/faktur-masukan/{created['id']}/credit", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["credit_status"] == "credited"
    assert data["credited_at"] is not None

    again = client.post(f"/api/v1/faktur-masukan/{created['id']}/credit", headers=headers)
    assert again.status_code == 409


def test_uncredit_reverts(client: TestClient):
    headers = auth_headers(client, SISWA)
    created = _create(client, headers)
    client.post(f"/api/v1/faktur-masukan/{created['id']}/credit", headers=headers)

    res = client.post(f"/api/v1/faktur-masukan/{created['id']}/uncredit", headers=headers)
    assert res.status_code == 200
    assert res.json()["credit_status"] == "not_credited"
    assert res.json()["credited_at"] is None

    again = client.post(f"/api/v1/faktur-masukan/{created['id']}/uncredit", headers=headers)
    assert again.status_code == 409


def test_bulk_credit_partial_success(client: TestClient):
    headers = auth_headers(client, SISWA)
    valid = _create(client, headers)
    already = _create(client, headers)
    client.post(f"/api/v1/faktur-masukan/{already['id']}/credit", headers=headers)

    other_headers = auth_headers(client, OTHER_SISWA)
    other = _create(client, other_headers)

    res = client.post(
        "/api/v1/faktur-masukan/bulk-credit",
        json={"ids": [valid["id"], already["id"], other["id"], 999_999]},
        headers=headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["credited"] == 1
    assert data["skipped"] == 3
    assert len(data["errors"]) == 3

    state = client.get(f"/api/v1/faktur-masukan/{valid['id']}", headers=headers).json()
    assert state["credit_status"] == "credited"


def test_bulk_uncredit_partial_success(client: TestClient):
    headers = auth_headers(client, SISWA)
    a = _create(client, headers)
    b = _create(client, headers)
    client.post(f"/api/v1/faktur-masukan/{a['id']}/credit", headers=headers)
    client.post(f"/api/v1/faktur-masukan/{b['id']}/credit", headers=headers)

    res = client.post(
        "/api/v1/faktur-masukan/bulk-uncredit",
        json={"ids": [a["id"], 999_999]},
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["uncredited"] == 1


def test_delete_only_draft_not_credited(client: TestClient):
    headers = auth_headers(client, SISWA)
    draft = _create(client, headers)
    removed = client.delete(f"/api/v1/faktur-masukan/{draft['id']}", headers=headers)
    assert removed.status_code == 204

    to_credit = _create(client, headers)
    client.post(f"/api/v1/faktur-masukan/{to_credit['id']}/credit", headers=headers)
    denied = client.delete(f"/api/v1/faktur-masukan/{to_credit['id']}", headers=headers)
    assert denied.status_code == 409


def test_siswa_cannot_credit_another_siswa_invoice(client: TestClient):
    owner_headers = auth_headers(client, SISWA)
    created = _create(client, owner_headers)

    intruder_headers = auth_headers(client, OTHER_SISWA)
    res = client.post(f"/api/v1/faktur-masukan/{created['id']}/credit", headers=intruder_headers)
    # Can_read fails -> 404 hide
    assert res.status_code == 404


def test_guru_can_review_but_siswa_cannot(client: TestClient):
    siswa_headers = auth_headers(client, SISWA)
    classes = client.get("/api/v1/classes", headers=siswa_headers).json()
    created = _create(client, siswa_headers, class_id=classes[0]["id"])

    guru_headers = auth_headers(client, GURU)
    res = client.patch(
        f"/api/v1/faktur-masukan/{created['id']}/review",
        json={"score": 90, "teacher_feedback": "Kredit sudah tepat"},
        headers=guru_headers,
    )
    assert res.status_code == 200
    assert res.json()["score"] == 90

    denied = client.patch(
        f"/api/v1/faktur-masukan/{created['id']}/review",
        json={"score": 100},
        headers=siswa_headers,
    )
    assert denied.status_code == 403


def test_guru_can_credit_and_uncredit_own_class_invoice(client: TestClient):
    siswa_headers = auth_headers(client, SISWA)
    class_id = client.get("/api/v1/classes", headers=siswa_headers).json()[0]["id"]
    created = _create(client, siswa_headers, class_id=class_id)

    guru_headers = auth_headers(client, GURU)
    res = client.post(f"/api/v1/faktur-masukan/{created['id']}/credit", headers=guru_headers)
    assert res.status_code == 200, res.text
    assert res.json()["credit_status"] == "credited"

    uncredit = client.post(f"/api/v1/faktur-masukan/{created['id']}/uncredit", headers=guru_headers)
    assert uncredit.status_code == 200, uncredit.text
    assert uncredit.json()["credit_status"] == "not_credited"


def test_guru_bulk_credit_own_class_invoice(client: TestClient):
    siswa_headers = auth_headers(client, SISWA)
    class_id = client.get("/api/v1/classes", headers=siswa_headers).json()[0]["id"]
    a = _create(client, siswa_headers, class_id=class_id)

    guru_headers = auth_headers(client, GURU)
    res = client.post(
        "/api/v1/faktur-masukan/bulk-credit",
        json={"ids": [a["id"]]},
        headers=guru_headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["credited"] == 1

    res2 = client.post(
        "/api/v1/faktur-masukan/bulk-uncredit",
        json={"ids": [a["id"]]},
        headers=guru_headers,
    )
    assert res2.status_code == 200, res2.text
    assert res2.json()["uncredited"] == 1


def test_summary_counts(client: TestClient):
    headers = auth_headers(client, SISWA)
    before = client.get("/api/v1/faktur-masukan/summary", headers=headers).json()
    _create(client, headers)
    after = client.get("/api/v1/faktur-masukan/summary", headers=headers).json()
    assert after["total"] == before["total"] + 1


def test_source_invoice_copy(client: TestClient):
    siswa_headers = auth_headers(client, SISWA)
    # Create and issue a keluaran source

    keluaran = client.post(
        "/api/v1/faktur-keluaran",
        json=keluaran_payload(transaction_code="01", lines=[keluaran_line()]),
        headers=siswa_headers,
    )
    assert keluaran.status_code == 201
    kid = keluaran.json()["id"]
    issued = client.post(
        f"/api/v1/faktur-keluaran/{kid}/issue",
        json={"kode_otorisasi": "123456"},
        headers=siswa_headers,
    )
    assert issued.status_code == 200
    source_number = issued.json()["invoice_number"]
    assert source_number is not None

    # Create masukan via source
    res = client.post(
        "/api/v1/faktur-masukan",
        json=_payload(source_invoice_id=kid, lines=[]),
        headers=siswa_headers,
    )
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["source_invoice_id"] == kid
    assert data["invoice_number"] == source_number
    assert len(data["lines"]) == 1


def test_source_must_be_issued(client: TestClient):
    siswa_headers = auth_headers(client, SISWA)

    keluaran = client.post("/api/v1/faktur-keluaran", json=keluaran_payload(), headers=siswa_headers)  # noqa: E501
    kid = keluaran.json()["id"]
    # Not issued yet
    res = client.post("/api/v1/faktur-masukan", json=_payload(source_invoice_id=kid), headers=siswa_headers)  # noqa: E501
    assert res.status_code == 409


def test_list_filters_by_credit_status(client: TestClient):
    headers = auth_headers(client, SISWA)
    a = _create(client, headers)
    b = _create(client, headers)
    client.post(f"/api/v1/faktur-masukan/{b['id']}/credit", headers=headers)

    res = client.get("/api/v1/faktur-masukan?credit_status=credited", headers=headers)
    assert res.status_code == 200
    items = res.json()["items"]
    assert all(item["credit_status"] == "credited" for item in items)
    assert any(item["id"] == b["id"] for item in items)
    assert not any(item["id"] == a["id"] for item in items)


def test_inaccessible_mutations_return_404(client: TestClient):
    owner = auth_headers(client, SISWA)
    created = _create(client, owner)

    intruder = auth_headers(client, OTHER_SISWA)
    patch = client.patch(f"/api/v1/faktur-masukan/{created['id']}", json={"buyer_name": "Hack"}, headers=intruder)  # noqa: E501
    assert patch.status_code == 404

    delete = client.delete(f"/api/v1/faktur-masukan/{created['id']}", headers=intruder)
    assert delete.status_code == 404

    credit = client.post(f"/api/v1/faktur-masukan/{created['id']}/credit", headers=intruder)
    assert credit.status_code == 404


# --- 008: prepopulated pajak masukan (opsi A: buyer_siswa_id) ----------------

BUYER_SISWA = "siswa2@smkn1-pku.local"


def _classmates(client: TestClient) -> tuple[int, int, int]:
    """Return (class_id, seller_siswa_id, buyer_siswa_id) for two classmates."""
    admin_headers = auth_headers(client, ADMIN)
    classes = client.get("/api/v1/classes", headers=admin_headers).json()
    assert classes, "seed membutuhkan minimal satu kelas untuk uji manual"
    detail = client.get(f"/api/v1/classes/{classes[0]['id']}", headers=admin_headers).json()
    students = detail.get("students", [])
    assert len(students) >= 2, "seed membutuhkan dua siswa sekelas untuk uji manual"
    return classes[0]["id"], students[0]["id"], students[1]["id"]


def _free_month(client: TestClient, headers: dict) -> int:
    """Pick a 2026 PPN period the buyer has not used yet (rerun-safe)."""
    res = client.get(
        "/api/v1/spt", params={"return_type": "ppn", "size": 100}, headers=headers
    )
    assert res.status_code == 200, res.text
    used = {row["tax_month"] for row in res.json()["items"] if row["tax_year"] == 2026}
    for month in (8, 11, 9, 10, 7, 6, 5, 4, 3, 2):
        if month not in used:
            return month
    raise AssertionError("no free PPN period left for the buyer fixture")


def _issue_keluaran_with_buyer(
    client: TestClient, headers: dict, class_id: int, buyer_id: int, **overrides
) -> dict:
    payload = keluaran_payload(
        transaction_code="01",
        lines=[keluaran_line()],
        class_id=class_id,
        buyer_siswa_id=buyer_id,
        **overrides,
    )
    created = client.post("/api/v1/faktur-keluaran", json=payload, headers=headers)
    assert created.status_code == 201, created.text
    issued = client.post(
        f"/api/v1/faktur-keluaran/{created.json()['id']}/issue",
        json={"kode_otorisasi": "123456"},
        headers=headers,
    )
    assert issued.status_code == 200, issued.text
    return issued.json()


def test_issue_with_classmate_buyer_prepopulates_masukan(client: TestClient):
    seller_headers = auth_headers(client, SISWA)
    buyer_headers = auth_headers(client, BUYER_SISWA)
    class_id, _, buyer_id = _classmates(client)

    issued = _issue_keluaran_with_buyer(
        client,
        seller_headers,
        class_id,
        buyer_id,
        invoice_date="2026-09-18",
        tax_month=9,
    )

    items = client.get("/api/v1/faktur-masukan", headers=buyer_headers).json()["items"]
    found = [m for m in items if m["source_invoice_id"] == issued["id"]]
    assert len(found) == 1
    masukan = found[0]
    assert masukan["origin"] == "prepopulated"
    assert masukan["status"] == "issued"
    assert masukan["credit_status"] == "not_credited"
    assert masukan["invoice_number"] == issued["invoice_number"]
    assert masukan["total_ppn"] == issued["total_ppn"]
    assert len(masukan["lines"]) == len(issued["lines"])
    assert masukan["siswa_id"] == buyer_id


def test_buyer_outside_class_rejected(client: TestClient):
    seller_headers = auth_headers(client, SISWA)
    admin_headers = auth_headers(client, ADMIN)
    class_id, _, _ = _classmates(client)
    res_users = client.get("/api/v1/users?role=siswa&size=100", headers=admin_headers)
    outsider = res_users.json()["items"]
    # siswa4 is seeded outside the seller's class
    other = next(u for u in outsider if u["email"] == OTHER_SISWA)
    res = client.post(
        "/api/v1/faktur-keluaran",
        json=keluaran_payload(class_id=class_id, buyer_siswa_id=other["id"]),
        headers=seller_headers,
    )
    assert res.status_code == 422, res.text


def test_buyer_self_rejected(client: TestClient):
    seller_headers = auth_headers(client, SISWA)
    class_id, seller_id, _ = _classmates(client)
    res = client.post(
        "/api/v1/faktur-keluaran",
        json=keluaran_payload(class_id=class_id, buyer_siswa_id=seller_id),
        headers=seller_headers,
    )
    assert res.status_code == 422, res.text


def test_tidak_kreditkan_and_kembali_approved(client: TestClient):
    seller_headers = auth_headers(client, SISWA)
    buyer_headers = auth_headers(client, BUYER_SISWA)
    class_id, _, buyer_id = _classmates(client)

    issued = _issue_keluaran_with_buyer(
        client,
        seller_headers,
        class_id,
        buyer_id,
        invoice_date="2026-09-19",
        tax_month=9,
    )
    items = client.get("/api/v1/faktur-masukan", headers=buyer_headers).json()["items"]
    masukan = next(m for m in items if m["source_invoice_id"] == issued["id"])

    url_tidak = f"/api/v1/faktur-masukan/{masukan['id']}/tidak-kreditkan"
    res = client.post(url_tidak, headers=buyer_headers)
    assert res.status_code == 200, res.text
    assert res.json()["credit_status"] == "uncredited"

    again = client.post(url_tidak, headers=buyer_headers)
    assert again.status_code == 409

    url_kembali = f"/api/v1/faktur-masukan/{masukan['id']}/kembali-approved"
    back = client.post(url_kembali, headers=buyer_headers)
    assert back.status_code == 200, back.text
    assert back.json()["credit_status"] == "not_credited"

    # Explicitly uncredited invoices can still be credited afterwards.
    client.post(f"/api/v1/faktur-masukan/{masukan['id']}/tidak-kreditkan", headers=buyer_headers)
    credited = client.post(f"/api/v1/faktur-masukan/{masukan['id']}/credit", headers=buyer_headers)
    assert credited.status_code == 200, credited.text
    assert credited.json()["credit_status"] == "credited"


def test_cancel_source_invalidates_derived(client: TestClient):
    seller_headers = auth_headers(client, SISWA)
    buyer_headers = auth_headers(client, BUYER_SISWA)
    admin_headers = auth_headers(client, ADMIN)
    class_id, _, buyer_id = _classmates(client)

    issued = _issue_keluaran_with_buyer(
        client,
        seller_headers,
        class_id,
        buyer_id,
        invoice_date="2026-10-18",
        tax_month=10,
    )
    items = client.get("/api/v1/faktur-masukan", headers=buyer_headers).json()["items"]
    masukan = next(m for m in items if m["source_invoice_id"] == issued["id"])

    res = client.post(
        f"/api/v1/faktur-keluaran/{issued['id']}/cancel",
        json={"reason": " salah tulis"},
        headers=admin_headers,
    )
    assert res.status_code == 200, res.text

    state = client.get(f"/api/v1/faktur-masukan/{masukan['id']}", headers=buyer_headers).json()
    assert state["status"] == "invalid"


def test_cancel_blocked_when_derived_in_reported_spt(client: TestClient):
    seller_headers = auth_headers(client, SISWA)
    buyer_headers = auth_headers(client, BUYER_SISWA)
    admin_headers = auth_headers(client, ADMIN)
    class_id, _, buyer_id = _classmates(client)

    month = _free_month(client, buyer_headers)
    issued = _issue_keluaran_with_buyer(
        client,
        seller_headers,
        class_id,
        buyer_id,
        invoice_date=f"2026-{month:02d}-18",
        tax_month=month,
    )
    items = client.get("/api/v1/faktur-masukan", headers=buyer_headers).json()["items"]
    masukan = next(m for m in items if m["source_invoice_id"] == issued["id"])
    credited_res = client.post(
        f"/api/v1/faktur-masukan/{masukan['id']}/credit", headers=buyer_headers
    )
    assert credited_res.status_code == 200, credited_res.text

    spt = client.post(
        "/api/v1/spt",
        json={"return_type": "ppn", "tax_month": month, "tax_year": 2026},
        headers=buyer_headers,
    )
    assert spt.status_code == 201, spt.text
    spt_id = spt.json()["id"]
    assert client.post(f"/api/v1/spt/{spt_id}/posting", headers=buyer_headers).status_code == 200
    assert client.post(
        f"/api/v1/spt/{spt_id}/pernyataan",
        json={
            "signer_name": "Direktur Simulasi",
            "signer_npwp": "0024400141421000",
            "signer_capacity": "wajib_pajak",
        },
        headers=buyer_headers,
    ).status_code == 200
    submit = client.post(
        f"/api/v1/spt/{spt_id}/submit",
        json={"passphrase": "Password123!", "lb_treatment": "kompensasi"},
        headers=buyer_headers,
    )
    assert submit.status_code == 200, submit.text

    res = client.post(
        f"/api/v1/faktur-keluaran/{issued['id']}/cancel",
        json={"reason": "coba batal"},
        headers=admin_headers,
    )
    assert res.status_code == 409, res.text


def test_spt_bagian_ii_counts_only_credited(client: TestClient):
    buyer_headers = auth_headers(client, BUYER_SISWA)
    month = _free_month(client, buyer_headers)
    invoice_date = f"2026-{month:02d}-18"
    credited = _create(client, buyer_headers, invoice_date=invoice_date, tax_month=month)
    ignored = _create(client, buyer_headers, invoice_date=invoice_date, tax_month=month)
    client.post(f"/api/v1/faktur-masukan/{credited['id']}/credit", headers=buyer_headers)
    res = client.post(
        f"/api/v1/faktur-masukan/{ignored['id']}/tidak-kreditkan", headers=buyer_headers
    )
    assert res.status_code == 200, res.text

    spt = client.post(
        "/api/v1/spt",
        json={"return_type": "ppn", "tax_month": month, "tax_year": 2026},
        headers=buyer_headers,
    )
    assert spt.status_code == 201, spt.text
    posted = client.post(f"/api/v1/spt/{spt.json()['id']}/posting", headers=buyer_headers)
    assert posted.status_code == 200, posted.text
    body = posted.json()
    assert body["masukan_ppn"] == credited["total_ppn"]
    assert body["masukan_dpp"] == credited["total_dpp"]
