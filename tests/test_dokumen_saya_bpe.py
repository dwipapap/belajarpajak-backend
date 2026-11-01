"""BPE SPT + Dokumen Saya tests (plan 010).

Months 2026-01..06 are owned by this module — the autouse fixture truncates
the SPT document tables first, the same pattern test_spt.py uses.
"""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session

from app.db import engine
from tests.conftest import auth_headers
from tests.test_spt import (
    OTHER_SISWA,
    SISWA,
    _create_spt,
    _make_bp23_issued,
    _make_keluaran_issued,
    _pernyataan,
    _posting,
    _submit,
)

BASE = "/api/v1"

BPE_RE = re.compile(r"^BPE-SIM-\d{5}/2026$")

_SPT_DOCUMENT_TABLES = (
    "billing_codes",
    "tax_return_lines",
    "tax_returns",
    "tax_invoice_lines",
    "tax_invoices",
    "withholding_slips",
)


@pytest.fixture(scope="module", autouse=True)
def _clean_tables() -> None:
    with Session(engine) as session:
        for table in _SPT_DOCUMENT_TABLES:
            session.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
        session.commit()


def _serial(bpe_number: str) -> int:
    return int(bpe_number[8:13])


def test_nihil_submit_issues_bpe(client: TestClient):
    """Period 2026-01: no sources -> nihil -> reported with BPE-SIM- number."""
    headers = auth_headers(client, SISWA)
    spt = _create_spt(client, headers, 1)
    _posting(client, headers, spt["id"])
    _pernyataan(client, headers, spt["id"])
    data = _submit(client, headers, spt["id"], status_code=200).json()
    assert data["status"] == "reported"
    assert data["bpe_number"] is not None and BPE_RE.match(data["bpe_number"])
    assert data["received_at"] is not None


def test_kurang_bayar_bpe_only_after_pay(client: TestClient):
    """Period 2026-02: BPE empty while awaiting payment, filled after pay."""
    headers = auth_headers(client, SISWA)
    _make_keluaran_issued(client, headers, 2)
    spt = _create_spt(client, headers, 2)
    _posting(client, headers, spt["id"])
    _pernyataan(client, headers, spt["id"])
    data = _submit(client, headers, spt["id"], status_code=200).json()
    assert data["status"] == "awaiting_payment"
    assert data["bpe_number"] is None

    bpe = client.get(f"{BASE}/spt/{spt['id']}/bpe", headers=headers)
    assert bpe.status_code == 409

    billing = client.get(f"{BASE}/spt/{spt['id']}/billing", headers=headers).json()
    pay = client.post(f"{BASE}/billings/{billing['id']}/pay", headers=headers)
    assert pay.status_code == 200, pay.text

    after = client.get(f"{BASE}/spt/{spt['id']}", headers=headers).json()
    assert after["status"] == "reported"
    assert after["bpe_number"] is not None and BPE_RE.match(after["bpe_number"])
    assert after["received_at"] is not None

    pdf = client.get(f"{BASE}/spt/{spt['id']}/bpe", headers=headers)
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")


def test_bpe_sequence_ascending_same_tenant_year(client: TestClient):
    """Periods 2026-03/04: two fresh reports get consecutive serials."""
    headers = auth_headers(client, SISWA)
    first, second = None, None
    for month in (3, 4):
        spt = _create_spt(client, headers, month)
        _posting(client, headers, spt["id"])
        _pernyataan(client, headers, spt["id"])
        data = _submit(client, headers, spt["id"], status_code=200).json()
        assert data["status"] == "reported", data
        if first is None:
            first = data["bpe_number"]
        else:
            second = data["bpe_number"]
    assert first is not None and second is not None
    assert _serial(second) == _serial(first) + 1


def test_pembetulan_clears_bpe_and_reissue_is_fresh(client: TestClient):
    """Period 2026-05: report -> pembetulan clears BPE -> re-report is new."""
    headers = auth_headers(client, SISWA)
    spt = _create_spt(client, headers, 5)
    _posting(client, headers, spt["id"])
    _pernyataan(client, headers, spt["id"])
    old = _submit(client, headers, spt["id"], status_code=200).json()
    assert old["status"] == "reported"
    old_bpe = old["bpe_number"]
    assert old_bpe is not None

    corrected = client.post(f"{BASE}/spt/{spt['id']}/pembetulan", headers=headers)
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["bpe_number"] is None
    assert corrected.json()["received_at"] is None

    # An intervening report consumes the freed serial, so the re-report below
    # deterministically gets a fresh number in isolated and full runs alike.
    mid = _create_spt(client, headers, 7)
    _posting(client, headers, mid["id"])
    _pernyataan(client, headers, mid["id"])
    mid_reported = _submit(client, headers, mid["id"], status_code=200).json()
    assert mid_reported["status"] == "reported"

    _posting(client, headers, spt["id"])
    new = _submit(client, headers, spt["id"], status_code=200).json()
    assert new["status"] == "reported"
    assert new["bpe_number"] is not None and new["bpe_number"] != old_bpe


def test_dokumen_saya_lists_and_scopes(client: TestClient):
    """Period 2026-06 nihil for siswa4: visible to owner only, filterable."""
    owner = auth_headers(client, OTHER_SISWA)
    spt = _create_spt(client, owner, 6)
    _posting(client, owner, spt["id"])
    _pernyataan(client, owner, spt["id"])
    reported = _submit(client, owner, spt["id"], status_code=200).json()
    assert reported["status"] == "reported"
    owner_bpe = reported["bpe_number"]

    slip = _make_bp23_issued(client, auth_headers(client, SISWA), 6)

    mine = auth_headers(client, SISWA)
    res = client.get(f"{BASE}/dokumen-saya", headers=mine)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] >= 1
    assert all(row["download_path"].startswith("/api/v1/") for row in body["items"])
    assert owner_bpe not in [row["nomor"] for row in body["items"]]

    bpe_only = client.get(f"{BASE}/dokumen-saya", params={"jenis": "bpe_spt"}, headers=mine)
    assert bpe_only.status_code == 200
    assert all(row["jenis"] == "bpe_spt" for row in bpe_only.json()["items"])
    assert bpe_only.json()["total"] >= 1

    slips = client.get(
        f"{BASE}/dokumen-saya", params={"jenis": "bukti_potong"}, headers=mine
    ).json()
    assert any(
        row["download_path"] == f"/api/v1/bp23/{slip['id']}/pdf" for row in slips["items"]
    )

    billings = client.get(
        f"{BASE}/dokumen-saya", params={"jenis": "kode_billing"}, headers=mine
    ).json()
    assert billings["total"] >= 1
    assert any("NTPN" in row["uraian"] for row in billings["items"])

    owner_list = client.get(f"{BASE}/dokumen-saya", headers=owner).json()
    assert owner_bpe in [row["nomor"] for row in owner_list["items"]]

    bad = client.get(f"{BASE}/dokumen-saya", params={"jenis": "kwitansi"}, headers=mine)
    assert bad.status_code == 422
