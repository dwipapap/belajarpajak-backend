"""Official tax-object catalog: endpoint, validation, rate, name and remap tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services import objek_pajak, objek_pajak_remap
from tests.conftest import auth_headers

SISWA = "siswa1@smkn1-pku.local"
BASE = "/api/v1"
SLIP_TYPES = ["bppu", "bp23", "sp", "cy", "bp21", "bp26", "bpnr", "bpmp"]


def _class_id(client: TestClient, headers: dict[str, str]) -> int:
    return client.get(f"{BASE}/classes", headers=headers).json()[0]["id"]


def _bppu(class_id: int, **over) -> dict:
    payload = {
        "class_id": class_id,
        "tax_month": 7,
        "tax_year": 2026,
        "recipient_identity_number": "1234567890123456",
        "recipient_name": "PT PENERIMA",
        "tax_type": "PPh 23",
        "tax_object_code": "24-104-03",
        "tax_nature": "non_final",
        "tax_facility": "none",
        "dpp": 10_000_000,
        "rate_percent": "2.00",
    }
    return {**payload, **over}


@pytest.mark.parametrize("slip_type", SLIP_TYPES)
def test_endpoint_returns_catalog_for_each_module(client: TestClient, slip_type: str) -> None:
    headers = auth_headers(client, SISWA)
    res = client.get(f"{BASE}/referensi/objek-pajak", params={"jenis": slip_type}, headers=headers)
    assert res.status_code == 200, res.text
    rows = res.json()
    assert rows
    assert set(rows[0]) == {
        "kode",
        "nama",
        "pasal",
        "sifat",
        "kap_kjs",
        "tarif",
        "tarif_manual",
        "pph_manual",
    }
    assert len(rows) == len(objek_pajak.list_objects(slip_type))
    if slip_type == "bp23":
        assert {r["pasal"] for r in rows} == {"Pasal 23"}


def test_endpoint_requires_auth_and_resolves_facility(client: TestClient) -> None:
    assert client.get(f"{BASE}/referensi/objek-pajak", params={"jenis": "bppu"}).status_code == 401
    headers = auth_headers(client, SISWA)
    default = client.get(
        f"{BASE}/referensi/objek-pajak", params={"jenis": "bppu"}, headers=headers
    ).json()
    lainnya = client.get(
        f"{BASE}/referensi/objek-pajak", params={"jenis": "bppu", "fasilitas": "8"}, headers=headers
    ).json()
    d = next(r for r in default if r["kode"] == "24-101-01")
    o = next(r for r in lainnya if r["kode"] == "24-101-01")
    assert d["nama"] == "Dividen" and d["tarif"] == 15 and d["tarif_manual"] is False
    assert o["tarif"] == 15 and o["tarif_manual"] is True


def test_official_codes_not_fake_ones() -> None:
    assert objek_pajak.get_object("bppu", "24-101-01")["nama"] == "Dividen"
    assert objek_pajak.get_object("bppu", "24-100-01")["nama"].startswith("Hadiah")
    assert objek_pajak.get_object("bppu", "28-100-01") is None
    assert objek_pajak.get_object("bp23", "28-409-10") is None  # bp23 is Pasal 23 only


def test_invalid_code_rejected(client: TestClient) -> None:
    headers = auth_headers(client, SISWA)
    res = client.post(
        f"{BASE}/bppu",
        headers=headers,
        json=_bppu(_class_id(client, headers), tax_object_code="28-100-01"),
    )
    assert res.status_code == 422
    assert res.json()["detail"] == "Kode objek 28-100-01 tidak ada di katalog BPU."


def test_catalog_rate_enforced_and_name_stored(client: TestClient) -> None:
    headers = auth_headers(client, SISWA)
    class_id = _class_id(client, headers)
    bad = client.post(f"{BASE}/bppu", headers=headers, json=_bppu(class_id, rate_percent="15.00"))
    assert bad.status_code == 422
    assert "harus 2%" in bad.json()["detail"]

    ok = client.post(f"{BASE}/bppu", headers=headers, json=_bppu(class_id, tax_object_name="x"))
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["tax_object_name"] == "Jasa Konsultan"
    assert body["kap_kjs"] == "411124-100"  # set automatically when omitted
    assert body["income_tax"] == 200_000

    wrong_kap = client.post(
        f"{BASE}/bppu", headers=headers, json=_bppu(class_id, kap_kjs="411128-100")
    )
    assert wrong_kap.status_code == 422


def test_manual_rate_facility_lainnya_allows_custom_rate(client: TestClient) -> None:
    headers = auth_headers(client, SISWA)
    res = client.post(
        f"{BASE}/bppu",
        headers=headers,
        json=_bppu(_class_id(client, headers), tax_facility="lainnya", rate_percent="1.00"),
    )
    assert res.status_code == 201, res.text
    assert res.json()["income_tax"] == 100_000


def test_no_npwp_double_rate_only_for_pasal_23(client: TestClient) -> None:
    headers = auth_headers(client, SISWA)
    class_id = _class_id(client, headers)
    p23 = client.post(
        f"{BASE}/bppu",
        headers=headers,
        json=_bppu(class_id, recipient_identity_number="0000000000000000"),
    )
    assert p23.status_code == 201, p23.text
    assert p23.json()["income_tax"] == 400_000
    assert p23.json()["calculation_method"] == "flat_x2"

    final = client.post(
        f"{BASE}/bppu",
        headers=headers,
        json=_bppu(
            class_id,
            recipient_identity_number="0000000000000000",
            tax_object_code="28-409-10",
            tax_type="PPh 4(2)",
            tax_nature="final",
        ),
    )
    assert final.status_code == 201, final.text
    assert final.json()["income_tax"] == 200_000
    assert final.json()["calculation_method"] == "flat"


def test_update_resets_name_and_kap_from_catalog(client: TestClient) -> None:
    headers = auth_headers(client, SISWA)
    created = client.post(f"{BASE}/bppu", headers=headers, json=_bppu(_class_id(client, headers)))
    slip_id = created.json()["id"]
    upd = client.patch(
        f"{BASE}/bppu/{slip_id}",
        headers=headers,
        json={"tax_object_code": "28-409-10", "tax_nature": "final"},
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["tax_object_name"].startswith("Jasa Konstruksi Berupa Jasa Pelaksanaan")
    assert upd.json()["kap_kjs"] == "411128-100"


def test_remap_targets_exist_and_chain_is_safe() -> None:
    assert set(objek_pajak_remap.REMAP) == set(SLIP_TYPES)
    for slip_type in SLIP_TYPES:
        rows = objek_pajak_remap.remap_rows(slip_type)  # raises if a target is missing
        assert len(rows) == len(objek_pajak_remap.REMAP[slip_type])
    # 24-100-01 -> 24-101-01 while 24-101-01 -> 24-102-01: each old code maps once.
    assert objek_pajak_remap.REMAP["bppu"]["24-100-01"] == "24-101-01"
    assert objek_pajak_remap.REMAP["bppu"]["24-101-01"] == "24-102-01"
    assert set(objek_pajak_remap.REMAP["bp26"].values()) == {"27-100-99"}
    assert objek_pajak_remap.REMAP["bppu"]["28-100-01"] == "28-409-10"


def _bpnr(class_id: int, **over) -> dict:
    payload = {
        "class_id": class_id,
        "tax_month": 7,
        "tax_year": 2026,
        "recipient_identity_number": "SG-12345678",
        "recipient_name": "ACME PTE LTD",
        "tax_type": "PPh 26",
        "tax_object_code": "27-104-01",
        "tax_nature": "final",
        "tax_facility": "treaty",
        "gross_income": 10_000_000,
        "dpp_percent": "100.00",
        "rate_percent": "10.00",
        "nomor_skd": "SKD-0001",
    }
    return {**payload, **over}


def test_skd_requires_receipt_number_and_uses_typed_rate(client: TestClient) -> None:
    headers = auth_headers(client, SISWA)
    class_id = _class_id(client, headers)
    missing = client.post(f"{BASE}/bpnr", headers=headers, json=_bpnr(class_id, nomor_skd=None))
    assert missing.status_code == 422
    assert "Receipt Number" in missing.text
    res = client.post(f"{BASE}/bpnr", headers=headers, json=_bpnr(class_id))
    assert res.status_code == 201, res.text
    assert res.json()["income_tax"] == 1_000_000
