"""SPT Masa PPN + PPh Unifikasi workflow tests.

Fixture helpers build issued faktur keluaran, credited faktur masukan, and
issued bp23 slips for dedicated periods away from the July 2026 seed data.
Because an SPT is unique per (tenant, type, period), every test owns its own
month — the map lives in the comments next to each test.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session

from app.db import engine
from tests.conftest import auth_headers
from tests.test_faktur_keluaran import _payload as keluaran_payload
from tests.test_faktur_masukan import _line as masukan_line
from tests.test_faktur_masukan import _payload as masukan_payload

SISWA = "siswa1@smkn1-pku.local"
OTHER_SISWA = "siswa4@smkn1-pku.local"
GURU = "guru1@smkn1-pku.local"
PASSPHRASE = "Password123!"

BASE = "/api/v1"

_SPT_DOCUMENT_TABLES = (
    "billing_codes",
    "tax_return_lines",
    "tax_returns",
    "tax_input_lines",
    "tax_input_invoices",
    "tax_invoice_lines",
    "tax_invoices",
    "withholding_slips",
)


@pytest.fixture(scope="module", autouse=True)
def _clean_spt_tables() -> None:
    """Start this module from a clean document slate.

    These tests build exact aggregates from their own fixtures, so leftover
    faktur/bupot/SPT from other test files (or previous runs) would pollute
    every posting assertion. Users/classes/tenants are left intact.
    """
    with Session(engine) as session:
        for table in _SPT_DOCUMENT_TABLES:
            session.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
        session.commit()


# --- fixture helpers ----------------------------------------------------------


def _make_keluaran_issued(
    client: TestClient, headers: dict, month: int, transaction_code: str = "01", year: int = 2026
) -> dict:
    res = client.post(
        f"{BASE}/faktur-keluaran",
        json=keluaran_payload(
            transaction_code=transaction_code,
            invoice_date=f"{year}-{month:02d}-10",
            tax_month=month,
            tax_year=year,
        ),
        headers=headers,
    )
    assert res.status_code == 201, res.text
    created = res.json()
    issued = client.post(f"{BASE}/faktur-keluaran/{created['id']}/issue", headers=headers)
    assert issued.status_code == 200, issued.text
    return issued.json()


def _make_masukan_credited(
    client: TestClient,
    headers: dict,
    month: int,
    year: int = 2026,
    lines: list[dict] | None = None,
) -> dict:
    res = client.post(
        f"{BASE}/faktur-masukan",
        json=masukan_payload(
            invoice_date=f"{year}-{month:02d}-08",
            tax_month=month,
            tax_year=year,
            **({"lines": lines} if lines is not None else {}),
        ),
        headers=headers,
    )
    assert res.status_code == 201, res.text
    created = res.json()
    credit = client.post(f"{BASE}/faktur-masukan/{created['id']}/credit", headers=headers)
    assert credit.status_code == 200, credit.text
    return credit.json()


def _make_bp23_issued(
    client: TestClient, headers: dict, month: int, gross: int = 10_000_000
) -> dict:
    res = client.post(
        f"{BASE}/bp23",
        json={
            "tax_month": month,
            "tax_year": 2026,
            "recipient_identity_number": "1234567890123456",
            "recipient_name": "Pemotongan Simulasi",
            "tax_object_code": "24-100-01",
            "gross_income": gross,
            "dpp_percent": 100,
            "rate_percent": 15,
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    created = res.json()
    issued = client.post(f"{BASE}/bp23/{created['id']}/issue", headers=headers)
    assert issued.status_code == 200, issued.text
    return issued.json()


def _create_spt(
    client: TestClient,
    headers: dict,
    month: int,
    return_type: str = "ppn",
    year: int = 2026,
    **overrides,
) -> dict:
    res = client.post(
        f"{BASE}/spt",
        json={
            "return_type": return_type,
            "tax_month": month,
            "tax_year": year,
            **overrides,
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


def _posting(client: TestClient, headers: dict, spt_id: int) -> dict:
    res = client.post(f"{BASE}/spt/{spt_id}/posting", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def _pernyataan(client: TestClient, headers: dict, spt_id: int) -> dict:
    res = client.post(
        f"{BASE}/spt/{spt_id}/pernyataan",
        json={
            "signer_name": "Direktur Simulasi",
            "signer_npwp": "0024400141421000",
            "signer_capacity": "wajib_pajak",
        },
        headers=headers,
    )
    assert res.status_code == 200, res.text
    return res.json()


def _submit(client: TestClient, headers: dict, spt_id: int, status_code: int | None = None) -> dict:
    res = client.post(
        f"{BASE}/spt/{spt_id}/submit",
        json={"passphrase": PASSPHRASE},
        headers=headers,
    )
    if status_code is not None:
        assert res.status_code == status_code, res.text
    return res


def _reported_ppn(client: TestClient, headers: dict, month: int, year: int = 2026) -> dict:
    """Full happy path for a PPN SPT that ends up reported."""
    spt = _create_spt(client, headers, month, year=year)
    _posting(client, headers, spt["id"])
    _pernyataan(client, headers, spt["id"])
    res = _submit(client, headers, spt["id"], status_code=200)
    return res.json()


# --- PPN tests ----------------------------------------------------------------


def test_create_ppn_draft_and_duplicate_conflict(client: TestClient):
    """Period 2026-02 (draft is deleted at the end to free the period)."""
    headers = auth_headers(client, SISWA)
    spt = _create_spt(client, headers, 2)

    assert spt["status"] == "draft"
    assert spt["return_type"] == "ppn"
    assert spt["correction_no"] == 0

    res = client.post(
        f"{BASE}/spt",
        json={"return_type": "ppn", "tax_month": 2, "tax_year": 2026},
        headers=headers,
    )
    assert res.status_code == 409

    # Pembetulan without a reported SPT for the period is rejected.
    res = client.post(
        f"{BASE}/spt",
        json={
            "return_type": "ppn",
            "tax_month": 2,
            "tax_year": 2026,
            "kind": "pembetulan",
        },
        headers=headers,
    )
    # The period row exists (draft) but was never reported -> 422.
    assert res.status_code in (409, 422)

    deleted = client.delete(f"{BASE}/spt/{spt['id']}", headers=headers)
    assert deleted.status_code == 204


def test_future_period_rejected(client: TestClient):
    headers = auth_headers(client, SISWA)
    res = client.post(
        f"{BASE}/spt",
        json={"return_type": "ppn", "tax_month": 12, "tax_year": 2099},
        headers=headers,
    )
    assert res.status_code == 422


def test_posting_ppn_aggregates_sources(client: TestClient):
    """Period 2026-03. Only the owner's issued/credited documents aggregate."""
    headers = auth_headers(client, SISWA)
    k1 = _make_keluaran_issued(client, headers, 3, transaction_code="01")
    k2 = _make_keluaran_issued(client, headers, 3, transaction_code="04")
    masukan = _make_masukan_credited(client, headers, 3)

    spt = _create_spt(client, headers, 3)
    posted = _posting(client, headers, spt["id"])

    assert posted["penyerahan_ppn"] == k1["total_ppn"] + k2["total_ppn"]
    assert posted["penyerahan_dpp"] == k1["total_dpp"] + k2["total_dpp"]
    assert posted["masukan_ppn"] == masukan["total_ppn"]
    assert posted["ppn_kurang_bayar"] == posted["penyerahan_ppn"] - posted["masukan_ppn"]
    assert posted["ppn_lebih_bayar"] == 0
    assert posted["amount_due"] == posted["ppn_kurang_bayar"]

    sections = {(line["section"], line["code"]) for line in posted["lines"]}
    assert ("I_A4", "01") in sections
    assert ("I_A2", "04") in sections
    assert ("II_C", "01") in sections

    # An issued but NOT credited masukan never lands in Bagian II.
    res = client.post(
        f"{BASE}/faktur-masukan",
        json=masukan_payload(invoice_date="2026-03-20", tax_month=3, tax_year=2026),
        headers=headers,
    )
    assert res.status_code == 201
    not_credited_id = res.json()["id"]
    posted_again = _posting(client, headers, spt["id"])
    assert posted_again["masukan_ppn"] == masukan["total_ppn"]
    client.delete(f"{BASE}/faktur-masukan/{not_credited_id}", headers=headers)

    # Another siswa's masukan is out of scope.
    other_headers = auth_headers(client, OTHER_SISWA)
    _make_masukan_credited(client, other_headers, 3)
    posted_third = _posting(client, headers, spt["id"])
    assert posted_third["masukan_ppn"] == masukan["total_ppn"]


def test_kompensasi_consumes_previous_overpayment(client: TestClient):
    """Periods 2026-01 (lebih bayar, reported) and 2026-02 (kompensasi)."""
    headers = auth_headers(client, SISWA)
    keluaran_jan = _make_keluaran_issued(client, headers, 1)
    masukan_jan = _make_masukan_credited(
        client, headers, 1, lines=[masukan_line(unit_price=2_000_000)]
    )
    assert masukan_jan["total_ppn"] > keluaran_jan["total_ppn"]

    jan = _reported_ppn(client, headers, 1)
    assert jan["status"] == "reported"
    lebih_jan = masukan_jan["total_ppn"] - keluaran_jan["total_ppn"]
    assert jan["ppn_lebih_bayar"] == lebih_jan

    # February consumes the January carryover as II.E.
    _make_keluaran_issued(client, headers, 2)
    _make_masukan_credited(client, headers, 2)
    spt_feb = _create_spt(client, headers, 2)
    posted = _posting(client, headers, spt_feb["id"])

    assert posted["kompensasi_lebih_bayar"] == lebih_jan
    line = next(x for x in posted["lines"] if x["section"] == "II_E")
    assert line["ppn"] == lebih_jan
    sisa = posted["penyerahan_ppn"] - posted["masukan_ppn"] - lebih_jan
    assert posted["ppn_kurang_bayar"] == max(sisa, 0)
    assert posted["ppn_lebih_bayar"] == max(-sisa, 0)


def test_submit_lebih_bayar_reports_immediately_without_billing(client: TestClient):
    """Period 2026-04: only masukan -> lebih bayar -> reported, no billing."""
    headers = auth_headers(client, SISWA)
    _make_masukan_credited(client, headers, 4)
    spt = _create_spt(client, headers, 4)
    _posting(client, headers, spt["id"])
    _pernyataan(client, headers, spt["id"])

    res = _submit(client, headers, spt["id"])
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "reported"
    assert data["submitted_at"] is not None

    billing = client.get(f"{BASE}/spt/{spt['id']}/billing", headers=headers)
    assert billing.status_code == 404


def test_submit_kurang_bayar_creates_billing_then_pay_reports(client: TestClient):
    """Period 2026-05: kurang bayar -> billing -> pay -> reported + flagged."""
    headers = auth_headers(client, SISWA)
    keluaran = _make_keluaran_issued(client, headers, 5)
    spt = _create_spt(client, headers, 5)
    _posting(client, headers, spt["id"])
    _pernyataan(client, headers, spt["id"])

    res = _submit(client, headers, spt["id"])
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "awaiting_payment"

    billing_res = client.get(f"{BASE}/spt/{spt['id']}/billing", headers=headers)
    assert billing_res.status_code == 200, billing_res.text
    billing = billing_res.json()
    assert billing["status"] == "unpaid"
    assert billing["amount"] == data["amount_due"] == keluaran["total_ppn"]
    assert len(billing["code"]) == 15 and billing["code"].isdigit()
    assert billing["kap_kjs"] == "411121-100"
    assert billing["masa_pajak"] == "052026"

    # The parked SPT cannot be re-submitted into a second billing.
    again = _submit(client, headers, spt["id"])
    assert again.status_code == 422
    billings = client.get(
        f"{BASE}/billings", params={"spt_id": spt["id"]}, headers=headers
    ).json()
    assert billings["total"] == 1

    # Paying flips the billing and the SPT to reported.
    pay = client.post(f"{BASE}/billings/{billing['id']}/pay", headers=headers)
    assert pay.status_code == 200, pay.text
    assert pay.json()["status"] == "paid"
    assert pay.json()["paid_at"] is not None

    after = client.get(f"{BASE}/spt/{spt['id']}", headers=headers).json()
    assert after["status"] == "reported"

    faktur_list = client.get(
        f"{BASE}/faktur-keluaran", params={"tax_month": 5, "tax_year": 2026}, headers=headers
    ).json()
    assert all(f["spt_flag"] == "reported_in_spt" for f in faktur_list["items"])


def test_paid_billing_cannot_be_paid_twice_or_by_others(client: TestClient):
    """Runs after the pay test: one paid billing exists for SISWA."""
    headers = auth_headers(client, SISWA)
    paid_list = client.get(
        f"{BASE}/billings", params={"status": "paid"}, headers=headers
    ).json()
    assert paid_list["total"] >= 1
    paid = paid_list["items"][0]

    res = client.post(f"{BASE}/billings/{paid['id']}/pay", headers=headers)
    assert res.status_code == 422

    intruder_headers = auth_headers(client, OTHER_SISWA)
    hidden = client.get(f"{BASE}/billings/{paid['id']}", headers=intruder_headers)
    assert hidden.status_code == 404
    denied = client.post(f"{BASE}/billings/{paid['id']}/pay", headers=intruder_headers)
    assert denied.status_code == 404


def test_wrong_passphrase_rejected(client: TestClient):
    """Period 2026-06."""
    headers = auth_headers(client, SISWA)
    _make_keluaran_issued(client, headers, 6)
    spt = _create_spt(client, headers, 6)
    _posting(client, headers, spt["id"])
    _pernyataan(client, headers, spt["id"])

    res = client.post(
        f"{BASE}/spt/{spt['id']}/submit", json={"passphrase": "salah"}, headers=headers
    )
    assert res.status_code == 401
    after = client.get(f"{BASE}/spt/{spt['id']}", headers=headers).json()
    assert after["status"] == "draft"


def test_submit_requires_pernyataan(client: TestClient):
    """Period 2026-08."""
    headers = auth_headers(client, SISWA)
    _make_keluaran_issued(client, headers, 8)
    spt = _create_spt(client, headers, 8)
    _posting(client, headers, spt["id"])

    res = _submit(client, headers, spt["id"])
    assert res.status_code == 422


def test_pernyataan_kuasa_requires_npwp(client: TestClient):
    """Period 2026-09."""
    headers = auth_headers(client, SISWA)
    spt = _create_spt(client, headers, 9)
    res = client.post(
        f"{BASE}/spt/{spt['id']}/pernyataan",
        json={"signer_name": "Kuasa Sim", "signer_capacity": "kuasa_wajib_pajak"},
        headers=headers,
    )
    assert res.status_code == 422


def test_pembetulan_flow(client: TestClient):
    """Period 2026-10 (reported) and 2025-12 (draft; Nov/Dec 2026 are future)."""
    headers = auth_headers(client, SISWA)
    _make_keluaran_issued(client, headers, 10)
    spt = _create_spt(client, headers, 10)
    _posting(client, headers, spt["id"])
    _pernyataan(client, headers, spt["id"])
    submitted = _submit(client, headers, spt["id"], status_code=200).json()
    if submitted["status"] == "awaiting_payment":
        billing = client.get(f"{BASE}/spt/{spt['id']}/billing", headers=headers).json()
        client.post(f"{BASE}/billings/{billing['id']}/pay", headers=headers)

    # Pembetulan on a draft is rejected — it needs a reported SPT first.
    draft_spt = _create_spt(client, headers, 12, year=2025)
    res = client.post(f"{BASE}/spt/{draft_spt['id']}/pembetulan", headers=headers)
    assert res.status_code == 422

    corrected = client.post(f"{BASE}/spt/{spt['id']}/pembetulan", headers=headers)
    assert corrected.status_code == 200, corrected.text
    data = corrected.json()
    assert data["status"] == "draft"
    assert data["kind"] == "pembetulan"
    assert data["correction_no"] == 1
    assert data["lines"] == []
    assert data["amount_due"] == 0
    assert data["submitted_at"] is None


def test_siswa_cannot_access_another_siswa_spt(client: TestClient):
    """Period 2024-12 (every other period is owned by a sibling test)."""
    owner_headers = auth_headers(client, SISWA)
    spt = _create_spt(client, owner_headers, 12, year=2024)

    intruder_headers = auth_headers(client, OTHER_SISWA)
    res = client.get(f"{BASE}/spt/{spt['id']}", headers=intruder_headers)
    assert res.status_code == 404


# --- PPh Unifikasi tests ------------------------------------------------------


def test_pph_posting_aggregates_bp23(client: TestClient):
    """Period 2026-02, PPh Unifikasi (separate uniqueness from PPN)."""
    headers = auth_headers(client, SISWA)
    slip = _make_bp23_issued(client, headers, 2, gross=10_000_000)
    assert slip["income_tax"] == 1_500_000

    spt = _create_spt(client, headers, 2, return_type="pph_unifikasi")
    posted = _posting(client, headers, spt["id"])

    assert posted["total_income_tax"] >= 1_500_000
    assert posted["amount_due"] == posted["total_income_tax"]
    kjs_100 = next(
        x for x in posted["lines"] if x["section"] == "pasal_23" and x["code"] == "411124-100"
    )
    assert kjs_100["pemotongan"] >= 1_500_000
    # Every known KJS row exists so the Induk keeps the real form's shape.
    assert len(posted["lines"]) >= 10


def test_pph_submit_creates_billing_with_pasal_kjs(client: TestClient):
    """Period 2026-03, PPh Unifikasi."""
    headers = auth_headers(client, SISWA)
    _make_bp23_issued(client, headers, 3, gross=4_000_000)
    spt = _create_spt(client, headers, 3, return_type="pph_unifikasi")
    _posting(client, headers, spt["id"])
    _pernyataan(client, headers, spt["id"])

    res = _submit(client, headers, spt["id"])
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "awaiting_payment"

    billing = client.get(f"{BASE}/spt/{spt['id']}/billing", headers=headers).json()
    assert billing["kap_kjs"] == "411124-100"
    assert billing["amount"] == data["total_income_tax"]


# --- summary & review ---------------------------------------------------------


def test_summary_counts_by_status(client: TestClient):
    """Period 2026-07 (the only month left; deleted afterwards to free it)."""
    headers = auth_headers(client, SISWA)
    before = client.get(
        f"{BASE}/spt/summary", params={"return_type": "ppn"}, headers=headers
    ).json()
    spt = _create_spt(client, headers, 7)
    after = client.get(
        f"{BASE}/spt/summary", params={"return_type": "ppn"}, headers=headers
    ).json()
    assert after["total"] == before["total"] + 1
    assert after["draft"] == before["draft"] + 1
    assert client.delete(f"{BASE}/spt/{spt['id']}", headers=headers).status_code == 204


def test_guru_can_review_spt(client: TestClient):
    """Period 2026-07, freed by the summary test."""
    siswa_headers = auth_headers(client, SISWA)
    classes = client.get(f"{BASE}/classes", headers=siswa_headers).json()
    spt = _create_spt(client, siswa_headers, 7, class_id=classes[0]["id"])

    guru_headers = auth_headers(client, GURU)
    res = client.patch(
        f"{BASE}/spt/{spt['id']}/review",
        json={"score": 85, "teacher_feedback": "Posting sudah benar"},
        headers=guru_headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["score"] == 85

    denied = client.patch(
        f"{BASE}/spt/{spt['id']}/review",
        json={"score": 100},
        headers=siswa_headers,
    )
    assert denied.status_code == 403


def test_terbilang():
    from app.services.billing_pdf import terbilang

    assert terbilang(0) == "nol"
    assert terbilang(1_000) == "seribu"
    assert terbilang(1_084_909) == (
        "satu juta delapan puluh empat ribu sembilan ratus sembilan"
    )
    assert terbilang(45_957_522) == (
        "empat puluh lima juta sembilan ratus lima puluh tujuh ribu "
        "lima ratus dua puluh dua"
    )
    assert terbilang(152_206) == "seratus lima puluh dua ribu dua ratus enam"


def test_billing_pdf_download(client: TestClient):
    """The paid billing (PPN, month 5) renders as a valid PDF."""
    headers = auth_headers(client, SISWA)
    paid_list = client.get(
        f"{BASE}/billings", params={"status": "paid"}, headers=headers
    ).json()
    assert paid_list["total"] >= 1
    billing = paid_list["items"][0]

    res = client.get(f"{BASE}/billings/{billing['id']}/pdf", headers=headers)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF-")
    assert "BILLING_" in res.headers["content-disposition"]

    # Another siswa cannot download someone else's billing.
    intruder_headers = auth_headers(client, OTHER_SISWA)
    denied = client.get(f"{BASE}/billings/{billing['id']}/pdf", headers=intruder_headers)
    assert denied.status_code == 404


def test_billing_pdf_pph_lists_kjs_rows(client: TestClient):
    """The PPh billing (month 3) detail table carries the pasal KJS row."""
    from sqlmodel import Session

    from app.db import engine
    from app.services.billing_pdf import billing_detail_rows, render_billing_pdf

    headers = auth_headers(client, SISWA)
    unpaid_list = client.get(
        f"{BASE}/billings", params={"status": "unpaid"}, headers=headers
    ).json()
    assert unpaid_list["total"] >= 1
    pph_billing = next(
        item for item in unpaid_list["items"] if item["kap_kjs"] == "411124-100"
    )

    with Session(engine) as session:
        from app.models.billing import BillingCode

        billing = session.get(BillingCode, pph_billing["id"])
        rows = billing_detail_rows(session, billing)
        assert rows == [("411124-100", pph_billing["masa_pajak"], pph_billing["amount"])]

        pdf_bytes = render_billing_pdf(session, billing)
        assert pdf_bytes.startswith(b"%PDF-")
