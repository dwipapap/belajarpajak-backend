"""Dompet deposit + Pbk + Tagihan + Angsuran (B1+B2+B3+C1+C2)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, select

from app.db import engine
from app.main import app

# Router baru belum didaftarkan di app/main.py (milik orkestrasi) — daftarkan
# sekali di sini agar alur ujung-ke-ujung dapat diuji tanpa menyentuh main.
from app.routers import angsuran as angsuran_router
from app.routers import deposit as deposit_router
from app.routers import pbk as pbk_router
from app.routers import tagihan as tagihan_router
from tests.conftest import auth_headers

if not any(getattr(r, "path", "") == "/api/v1/deposit" for r in app.routes):
    app.include_router(deposit_router.router, prefix="/api/v1")
    app.include_router(pbk_router.router, prefix="/api/v1")
    app.include_router(tagihan_router.router, prefix="/api/v1")
    app.include_router(angsuran_router.router, prefix="/api/v1")

SISWA = "siswa1@smkn1-pku.local"
GURU = "guru1@smkn1-pku.local"
ADMIN = "admin@smkn1-pku.local"
PASSPHRASE = "Password123!"
BASE = "/api/v1"

_NEW_TABLES = (
    "cicilan",
    "angsuran",
    "tagihan",
    "pbk_requests",
    "deposit_mutations",
    "deposit_accounts",
)


@pytest.fixture(scope="module", autouse=True)
def _clean_new_tables():
    with Session(engine) as session:
        for table in _NEW_TABLES:
            session.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
        session.commit()


def _me_id(client: TestClient, headers: dict) -> int:
    return client.get(f"{BASE}/auth/me", headers=headers).json()["id"]


def _insert_paid_deposit_billing(client_headers_amount: tuple, amount: int = 1_000_000):
    """Sisipkan billing mandiri deposit 411618-100 yang paid (KAP di luar whitelist).

    Serial memakai nilai kecil tak-jenuh (max+1, mengabaikan artefak 99999
    dari helper lama) agar allocator serial tidak jenuh untuk tenant itu.
    """
    from sqlmodel import select

    from app.models.billing import BillingCode
    from app.models.enums import BillingStatus

    headers, siswa_id, tenant_id = client_headers_amount
    with Session(engine) as session:
        # Bersihkan artefak helper lama yang menjenuhkan allocator (akhiran 99999).
        stale = session.exec(
            select(BillingCode).where(
                BillingCode.tenant_id == tenant_id,
                BillingCode.code.like("%99999"),
            )
        ).all()
        for row in stale:
            session.delete(row)
        session.flush()
        used = session.exec(
            select(BillingCode.code).where(BillingCode.tenant_id == tenant_id)
        ).all()
        serials = [
            int(c[-5:])
            for c in used
            if len(c) == 15 and c[-5:].isdigit() and int(c[-5:]) < 99_999
        ]
        serial = max(serials, default=0) + 1
        billing = BillingCode(
            tenant_id=tenant_id,
            siswa_id=siswa_id,
            spt_id=None,
            code=f"411618{(datetime.now(tz=UTC).strftime('%m%y'))}{serial:05d}",
            status=BillingStatus.paid,
            amount=amount,
            kap_kjs="411618-100",
            masa_pajak="012026",
            paid_at=datetime.now(tz=UTC),
        )
        session.add(billing)
        session.commit()
        session.refresh(billing)
        return billing.id


# --- B1 dompet ---------------------------------------------------------------


def test_deposit_saldo_awal_nol(client: TestClient) -> None:
    headers = auth_headers(client, SISWA)
    res = client.get(f"{BASE}/deposit", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["saldo"] == 0


def test_deposit_sinkron_idempoten(client: TestClient) -> None:
    headers = auth_headers(client, SISWA)
    me = client.get(f"{BASE}/auth/me", headers=headers).json()
    _insert_paid_deposit_billing((headers, me["id"], me["tenant_id"]), amount=500_000)
    first = client.post(f"{BASE}/deposit/sinkron", headers=headers).json()
    assert first["dikreditkan"] == 500_000
    second = client.post(f"{BASE}/deposit/sinkron", headers=headers).json()
    assert second["dikreditkan"] == 0
    assert client.get(f"{BASE}/deposit", headers=headers).json()["saldo"] == 500_000


def test_deposit_tenant_rekap_guru(client: TestClient) -> None:
    guru_headers = auth_headers(client, GURU)
    siswa_headers = auth_headers(client, SISWA)
    me = client.get(f"{BASE}/auth/me", headers=siswa_headers).json()
    res = client.get(f"{BASE}/deposit/tenant", headers=guru_headers)
    assert res.status_code == 200, res.text
    rows = {r["user_id"]: r for r in res.json()}
    assert me["id"] in rows
    # Siswa tidak boleh membuka rekap.
    assert client.get(f"{BASE}/deposit/tenant", headers=siswa_headers).status_code == 403


# --- B3 pbk ------------------------------------------------------------------


def test_pbk_flow(client: TestClient) -> None:
    siswa_headers = auth_headers(client, SISWA)
    guru_headers = auth_headers(client, GURU)
    created = client.post(
        f"{BASE}/pbk",
        json={
            "dari_kap": "411121-100",
            "ke_kap": "411124-100",
            "amount": 100_000,
            "masa": "012026",
            "alasan": "Salah setor KAP",
        },
        headers=siswa_headers,
    )
    assert created.status_code == 201, created.text
    pbk_id = created.json()["id"]
    assert created.json()["status"] == "diajukan"
    # Siswa melihat miliknya sendiri.
    mine = client.get(f"{BASE}/pbk", headers=siswa_headers).json()
    assert any(p["id"] == pbk_id for p in mine["items"])
    # Guru menyetujui; tanpa gerakan saldo (disetujui murni pencatatan).
    ok = client.post(f"{BASE}/pbk/{pbk_id}/setujui", headers=guru_headers)
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "disetujui"
    # Tidak ada mutasi 'pakai' yang tercatat.
    mutasi = client.get(f"{BASE}/deposit", headers=siswa_headers).json()["mutasi"]
    assert all(m["ref_type"] != "pbk" for m in mutasi)
    # Tolak setelah disetujui → 422.
    assert client.post(f"{BASE}/pbk/{pbk_id}/tolak", headers=guru_headers).status_code == 422


def test_pbk_tenant_scope_guru(client: TestClient) -> None:
    guru_headers = auth_headers(client, GURU)
    res = client.get(f"{BASE}/pbk", headers=guru_headers)
    assert res.status_code == 200, res.text
    assert res.json()["total"] >= 1


# --- C1 tagihan ---------------------------------------------------------------


def test_tagihan_flow(client: TestClient) -> None:
    admin_headers = auth_headers(client, ADMIN)
    siswa_headers = auth_headers(client, SISWA)
    siswa_id = _me_id(client, siswa_headers)
    created = client.post(
        f"{BASE}/tagihan",
        json={
            "siswa_id": siswa_id,
            "jenis": "skp",
            "kap_kjs": "411124-100",
            "masa": "022026",
            "amount": 250_000,
        },
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    tagihan = created.json()
    assert tagihan["status"] == "belum"
    # Siswa tidak boleh menerbitkan tagihan.
    assert (
        client.post(f"{BASE}/tagihan", json={**created.json()}, headers=siswa_headers).status_code
        in (403, 422)
    )
    # Buat billing.
    billed = client.post(
        f"{BASE}/tagihan/{tagihan['id']}/buat-billing", headers=siswa_headers
    )
    assert billed.status_code == 200, billed.text
    assert billed.json()["status"] == "dibilling"
    billing_id = billed.json()["billing_id"]
    # Lunaskan sebelum pay → 422.
    assert (
        client.post(
            f"{BASE}/tagihan/{tagihan['id']}/lunaskan-dari-billing",
            json={"billing_id": billing_id},
            headers=siswa_headers,
        ).status_code
        == 422
    )
    # Bayar billing lewat endpoint pay yang ada, lalu lunaskan.
    pay = client.post(f"{BASE}/billings/{billing_id}/pay", headers=siswa_headers)
    assert pay.status_code == 200, pay.text
    done = client.post(
        f"{BASE}/tagihan/{tagihan['id']}/lunaskan-dari-billing",
        json={"billing_id": billing_id},
        headers=siswa_headers,
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "lunas"


# --- C2 angsuran ---------------------------------------------------------------


def _mandiri_unpaid(client: TestClient, headers: dict, amount: int = 1_200_000) -> dict:
    res = client.post(
        f"{BASE}/billings",
        json={
            "kap_kjs": "411124-100",
            "masa_pajak": "032026",
            "amount": amount,
            "uraian": "Untuk angsuran",
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_angsuran_flow(client: TestClient) -> None:
    headers = auth_headers(client, SISWA)
    billing = _mandiri_unpaid(client, headers)
    created = client.post(
        f"{BASE}/angsuran", json={"billing_id": billing["id"], "n": 3}, headers=headers
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "berjalan"
    assert len(body["cicilan"]) == 3
    assert sum(c["amount"] for c in body["cicilan"]) == billing["amount"]
    jatuh = [c["jatuh_tempo"] for c in body["cicilan"]]
    assert jatuh == sorted(jatuh) and len(set(jatuh)) == 3
    # Angsuran ganda atas billing sama ditolak.
    assert (
        client.post(
            f"{BASE}/angsuran", json={"billing_id": billing["id"], "n": 2}, headers=headers
        ).status_code
        == 422
    )
    # Bayar semua cicilan → angsuran lunas.
    for c in body["cicilan"]:
        pay = client.post(
            f"{BASE}/angsuran/cicilan/{c['id']}/pay", json={"kanal": "teller"}, headers=headers
        )
        assert pay.status_code == 200, pay.text
    final = client.get(f"{BASE}/angsuran/{body['id']}", headers=headers).json()
    assert final["status"] == "lunas"
    assert all(c["status"] == "lunas" for c in final["cicilan"])
    # Cicilan ganda → 422.
    assert (
        client.post(
            f"{BASE}/angsuran/cicilan/{body['cicilan'][0]['id']}/pay",
            json={"kanal": "teller"},
            headers=headers,
        ).status_code
        == 422
    )


def test_angsuran_blokir_billing_paid(client: TestClient) -> None:
    headers = auth_headers(client, SISWA)
    billing = _mandiri_unpaid(client, headers, amount=100_000)
    pay = client.post(f"{BASE}/billings/{billing['id']}/pay", headers=headers)
    assert pay.status_code == 200
    res = client.post(
        f"{BASE}/angsuran", json={"billing_id": billing["id"], "n": 2}, headers=headers
    )
    assert res.status_code == 422


def test_angsuran_guru_tenant_scope(client: TestClient) -> None:
    guru_headers = auth_headers(client, GURU)
    res = client.get(f"{BASE}/angsuran", headers=guru_headers)
    assert res.status_code == 200, res.text
    assert res.json()["total"] >= 1


# --- B2 submit via deposit ------------------------------------------------------


def _make_bp23_issued_unifikasi(
    client: TestClient, headers: dict, month: int, year: int, gross: int
) -> dict:
    """Bukti potong PPh 23 terbit untuk periode SPT unifikasi (dpp 100%, tarif 15%)."""
    res = client.post(
        f"{BASE}/bp23",
        json={
            "tax_month": month,
            "tax_year": year,
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
    issued = client.post(
        f"{BASE}/bp23/{created['id']}/issue",
        headers=headers,
        json={"kode_otorisasi": "KO-SIM-123"},
    )
    assert issued.status_code == 200, issued.text
    return issued.json()


def test_spt_submit_deposit_saldo_kurang_dan_cukup(client: TestClient) -> None:
    from app.models.enums import TaxReturnType
    from app.models.slip import WithholdingSlip
    from app.models.tax_return import TaxReturn

    headers = auth_headers(client, SISWA)
    me = client.get(f"{BASE}/auth/me", headers=headers).json()
    tenant_id, siswa_id = me["tenant_id"], me["id"]

    # DB tes dipakai bersama antar run: bersihkan sisa SPT/slip periode ini
    # agar posting hanya mengagregat dokumen skenario ini (deterministik).
    with Session(engine) as session:
        for spt in session.exec(
            select(TaxReturn).where(
                TaxReturn.tenant_id == tenant_id,
                TaxReturn.siswa_id == siswa_id,
                TaxReturn.return_type == TaxReturnType.pph_unifikasi,
                TaxReturn.tax_year == 2025,
                TaxReturn.tax_month.in_([10, 11]),
            )
        ).all():
            session.delete(spt)
        for slip in session.exec(
            select(WithholdingSlip).where(
                WithholdingSlip.tenant_id == tenant_id,
                WithholdingSlip.siswa_id == siswa_id,
                WithholdingSlip.tax_year == 2025,
                WithholdingSlip.tax_month.in_([10, 11]),
            )
        ).all():
            session.delete(slip)
        session.commit()

    # Submit me-recompute via posting, jadi SPT butuh dokumen riil agar
    # amount_due-nya survive (tanpa dokumen = nihil = langsung reported).
    saldo = client.get(f"{BASE}/deposit", headers=headers).json()["saldo"]
    total_pajak = 0
    gross = (saldo + 2_000_000) * 10
    for _ in range(3):
        total_pajak += _make_bp23_issued_unifikasi(client, headers, 10, 2025, gross)[
            "income_tax"
        ]
        if total_pajak > saldo:
            break
    assert total_pajak > saldo

    created = client.post(
        f"{BASE}/spt",
        json={"return_type": "pph_unifikasi", "tax_month": 10, "tax_year": 2025},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    spt_id = created.json()["id"]
    pernyataan = client.post(
        f"{BASE}/spt/{spt_id}/pernyataan",
        json={
            "signer_name": "Siswa Satu",
            "signer_npwp": "0024400141421000",
            "signer_capacity": "wajib_pajak",
        },
        headers=headers,
    )
    assert pernyataan.status_code == 200, pernyataan.text

    # Saldo kurang → 422 lunas-sekaligus.
    poor = client.post(
        f"{BASE}/spt/{spt_id}/submit?metode=deposit",
        json={"passphrase": PASSPHRASE},
        headers=headers,
    )
    assert poor.status_code == 422, poor.text
    assert "deposit" in poor.text.lower() or "saldo" in poor.text.lower()

    # Top-up secukupnya lalu submit ulang SPT yang sama → reported via deposit.
    kurang = total_pajak - client.get(f"{BASE}/deposit", headers=headers).json()["saldo"]
    _insert_paid_deposit_billing((headers, siswa_id, tenant_id), amount=kurang + 100_000)
    sink = client.post(f"{BASE}/deposit/sinkron", headers=headers)
    assert sink.status_code == 200, sink.text
    ok = client.post(
        f"{BASE}/spt/{spt_id}/submit?metode=deposit",
        json={"passphrase": PASSPHRASE},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "reported"
    with Session(engine) as session:
        row = session.get(TaxReturn, spt_id)
        assert row.payment_ref is not None and row.payment_ref.startswith("DEPOSIT:")
    # Tidak ada billing yang diterbitkan untuk SPT deposit.
    assert (
        client.get(f"{BASE}/spt/{spt_id}/billing", headers=headers).status_code == 404
    )
