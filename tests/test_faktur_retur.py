"""Retur Pajak Masukan / Retur Pajak Keluaran."""

import uuid

from fastapi.testclient import TestClient

from tests.conftest import auth_headers
from tests.test_faktur_keluaran import _line as keluaran_line
from tests.test_faktur_keluaran import _payload as keluaran_payload
from tests.test_faktur_masukan import BUYER_SISWA, SISWA, _classmates

RETUR = "/api/v1/retur-pajak-masukan"
RETUR_PK = "/api/v1/retur-pajak-keluaran"


def _issue_keluaran_with_buyer(client, headers, class_id, buyer_id, **overrides) -> dict:
    payload = keluaran_payload(class_id=class_id, buyer_siswa_id=buyer_id, **overrides)
    created = client.post("/api/v1/faktur-keluaran", json=payload, headers=headers)
    assert created.status_code == 201, created.text
    issued = client.post(
        f"/api/v1/faktur-keluaran/{created.json()['id']}/issue",
        json={"kode_otorisasi": "123456"},
        headers=headers,
    )
    assert issued.status_code == 200, issued.text
    return issued.json()


def _number() -> str:
    return f"RTR-{uuid.uuid4().hex[:10]}"


def _used_months(client: TestClient, headers: dict) -> set[int]:
    res = client.get("/api/v1/spt", params={"return_type": "ppn", "size": 100}, headers=headers)
    assert res.status_code == 200, res.text
    return {row["tax_month"] for row in res.json()["items"] if row["tax_year"] == 2026}


def _month_free_for_both(client: TestClient, seller: dict, buyer: dict) -> int:
    used = _used_months(client, seller) | _used_months(client, buyer)
    for month in (1, 2, 3, 4, 5, 6, 8, 9, 10, 11):
        if month not in used:
            return month
    raise AssertionError("no free PPN period left for the retur fixtures")


def _issue_for_buyer(
    client: TestClient, month: int, day: int = 10
) -> tuple[dict, dict, dict, dict]:
    """Seller issues 4 x Rp 500.000 to a classmate; returns headers and both fakturs."""
    seller = auth_headers(client, SISWA)
    buyer = auth_headers(client, BUYER_SISWA)
    class_id, _, buyer_id = _classmates(client)
    issued = _issue_keluaran_with_buyer(
        client,
        seller,
        class_id,
        buyer_id,
        invoice_date=f"2026-{month:02d}-{day:02d}",
        tax_month=month,
        lines=[keluaran_line(unit_price=500_000, quantity=4)],
    )
    items = client.get("/api/v1/faktur-masukan", headers=buyer).json()["items"]
    masukan = next(m for m in items if m["source_invoice_id"] == issued["id"])
    return seller, buyer, issued, masukan


def _create_retur(client, headers, masukan, *, qty, date, number=None, expect=201, **extra):
    body = {
        "input_invoice_id": masukan["id"],
        "return_number": number or _number(),
        "return_date": date,
        "lines": [{"input_line_id": masukan["lines"][0]["id"], "return_quantity": qty}],
        **extra,
    }
    res = client.post(RETUR, json=body, headers=headers)
    assert res.status_code == expect, res.text
    return res.json()


def _issue_retur(client, headers, retur_id, expect=200):
    res = client.post(
        f"{RETUR}/{retur_id}/issue", json={"kode_otorisasi": "123456"}, headers=headers
    )
    assert res.status_code == expect, res.text
    return res.json()


def test_retur_lifecycle_quantity_limit_and_seller_view(client: TestClient):
    seller, buyer, issued, masukan = _issue_for_buyer(client, month=7)
    date = "2026-07-20"

    # Retur 1 of 4: DPP 500.000 -> PPN 12% = 60.000
    draft = _create_retur(client, buyer, masukan, qty=1, date=date)
    assert draft["status"] == "draft"
    assert draft["total_dpp"] == 500_000
    assert draft["total_ppn"] == 60_000
    assert draft["lines"][0]["max_return_quantity"] == 4
    assert draft["invoice_number"] == issued["invoice_number"]

    # Draft is not visible to the seller yet.
    before = client.get(RETUR_PK, headers=seller).json()["items"]
    assert all(r["id"] != draft["id"] for r in before)

    # Quantity above the original is rejected.
    _create_retur(client, buyer, masukan, qty=5, date=date, expect=422)

    # Draft can be edited and recalculated.
    patched = client.patch(
        f"{RETUR}/{draft['id']}",
        json={"lines": [{"input_line_id": masukan["lines"][0]["id"], "return_quantity": 3}]},
        headers=buyer,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["total_ppn"] == 180_000

    signed = _issue_retur(client, buyer, draft["id"])
    assert signed["status"] == "issued"
    assert signed["status_faktur"] == "APPROVED"
    assert signed["electronic_signature_status"] == "signed"

    # Remaining quantity is 1: returning 2 more is rejected, 1 is accepted.
    _create_retur(client, buyer, masukan, qty=2, date=date, expect=422)
    second = _create_retur(client, buyer, masukan, qty=1, date=date)
    assert second["lines"][0]["max_return_quantity"] == 1
    assert second["lines"][0]["already_returned_quantity"] == 3

    # Issued retur cannot be edited or deleted; drafts can be deleted.
    assert client.delete(f"{RETUR}/{draft['id']}", headers=buyer).status_code == 409
    assert client.delete(f"{RETUR}/{second['id']}", headers=buyer).status_code == 204

    # The seller sees the issued retur in Retur Pajak Keluaran, read-only.
    listed = client.get(RETUR_PK, headers=seller).json()["items"]
    assert any(r["id"] == draft["id"] and r["buyer_name"] for r in listed)
    assert client.get(f"{RETUR_PK}/{draft['id']}", headers=seller).status_code == 200
    # ... and the buyer does not (they are not the seller of that faktur).
    other = client.get(RETUR_PK, headers=buyer).json()["items"]
    assert all(r["id"] != draft["id"] for r in other)
    assert client.post(RETUR_PK, json={}, headers=seller).status_code == 405

    # Buyer list shows it; the seller's masukan list does not.
    mine = client.get(RETUR, headers=buyer).json()["items"]
    assert any(r["id"] == draft["id"] for r in mine)
    assert all(r["id"] != draft["id"] for r in client.get(RETUR, headers=seller).json()["items"])


def test_retur_date_must_not_precede_invoice_date(client: TestClient):
    _, buyer, _, masukan = _issue_for_buyer(client, month=7, day=15)
    res = _create_retur(client, buyer, masukan, qty=1, date="2026-07-14", expect=422)
    assert "tanggal" in res["detail"].lower()
    # Same day as the faktur is allowed.
    _create_retur(client, buyer, masukan, qty=1, date="2026-07-15")


def test_retur_number_unique_and_issue_requires_otorisasi(client: TestClient):
    _, buyer, _, masukan = _issue_for_buyer(client, month=7)
    number = _number()
    draft = _create_retur(client, buyer, masukan, qty=1, date="2026-07-20", number=number)
    _create_retur(client, buyer, masukan, qty=1, date="2026-07-20", number=number, expect=409)
    res = client.post(f"{RETUR}/{draft['id']}/issue", json={"kode_otorisasi": "1"}, headers=buyer)
    assert res.status_code == 422


def test_retur_other_siswa_cannot_touch(client: TestClient):
    seller, buyer, _, masukan = _issue_for_buyer(client, month=7)
    draft = _create_retur(client, buyer, masukan, qty=1, date="2026-07-20")
    assert client.get(f"{RETUR}/{draft['id']}", headers=seller).status_code == 404
    assert client.delete(f"{RETUR}/{draft['id']}", headers=seller).status_code == 404
    body = {
        "input_invoice_id": masukan["id"],
        "return_number": _number(),
        "return_date": "2026-07-20",
        "lines": [{"input_line_id": masukan["lines"][0]["id"], "return_quantity": 1}],
    }
    assert client.post(RETUR, json=body, headers=seller).status_code == 404


def test_retur_dpp_nilai_lain_uses_eleven_twelfths(client: TestClient):
    seller = auth_headers(client, SISWA)
    buyer = auth_headers(client, BUYER_SISWA)
    class_id, _, buyer_id = _classmates(client)
    issued = _issue_keluaran_with_buyer(
        client,
        seller,
        class_id,
        buyer_id,
        transaction_code="04",
        invoice_date="2026-07-10",
        tax_month=7,
        lines=[keluaran_line(unit_price=1_000_000, quantity=1, use_dpp_other=True)],
    )
    items = client.get("/api/v1/faktur-masukan", headers=buyer).json()["items"]
    masukan = next(m for m in items if m["source_invoice_id"] == issued["id"])
    retur = _create_retur(client, buyer, masukan, qty=1, date="2026-07-10")
    assert retur["total_dpp"] == 1_000_000
    assert retur["total_dpp_other"] == 916_667
    assert retur["total_ppn"] == 110_000


def test_retur_reduces_spt_ppn_for_buyer_and_seller(client: TestClient):
    seller = auth_headers(client, SISWA)
    buyer = auth_headers(client, BUYER_SISWA)
    month = _month_free_for_both(client, seller, buyer)
    seller, buyer, issued, masukan = _issue_for_buyer(client, month=month)
    credit = client.post(f"/api/v1/faktur-masukan/{masukan['id']}/credit", headers=buyer)
    assert credit.status_code == 200, credit.text

    def keluaran_ppn() -> int:
        rows = client.get(
            "/api/v1/faktur-keluaran",
            params={"tax_month": month, "tax_year": 2026, "status_faktur": "APPROVED", "size": 100},
            headers=seller,
        ).json()["items"]
        return sum(r["total_ppn"] for r in rows)

    def masukan_ppn() -> int:
        rows = client.get(
            "/api/v1/faktur-masukan",
            params={"tax_month": month, "tax_year": 2026, "status_faktur": "CREDITED", "size": 100},
            headers=buyer,
        ).json()["items"]
        return sum(r["total_ppn"] for r in rows)

    # Retur of 1 of 4 units issued in the same period: PPN 60.000.
    retur = _create_retur(client, buyer, masukan, qty=1, date=f"2026-{month:02d}-20")
    assert retur["is_creditable"] is True  # the masukan is credited
    _issue_retur(client, buyer, retur["id"])

    def post(headers) -> dict:
        spt = client.post(
            "/api/v1/spt",
            json={"return_type": "ppn", "tax_month": month, "tax_year": 2026},
            headers=headers,
        )
        assert spt.status_code == 201, spt.text
        posted = client.post(f"/api/v1/spt/{spt.json()['id']}/posting", headers=headers)
        assert posted.status_code == 200, posted.text
        return posted.json()

    buyer_spt = post(buyer)
    assert buyer_spt["masukan_ppn"] == masukan_ppn() - 60_000
    seller_spt = post(seller)
    assert seller_spt["penyerahan_ppn"] == keluaran_ppn() - 60_000
