"""CRUD tests for simulator tariff tables."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import auth_headers

ADMIN_A = "admin@smkn1-pku.local"
SUPERADMIN = "super@pajaksim.local"


def _cleanup_year(client: TestClient, headers: dict[str, str], tahun_pajak: int) -> None:
    for item in client.get(
        f"/api/v1/tarif-pajak/ptkp?tahun_pajak={tahun_pajak}", headers=headers
    ).json():
        client.delete(f"/api/v1/tarif-pajak/ptkp/{item['id']}", headers=headers)
    for item in client.get(
        f"/api/v1/tarif-pajak/progresif?tahun_pajak={tahun_pajak}", headers=headers
    ).json():
        client.delete(f"/api/v1/tarif-pajak/progresif/{item['id']}", headers=headers)


def test_superadmin_can_create_update_delete_ptkp_tier(client: TestClient) -> None:
    headers = auth_headers(client, SUPERADMIN)
    _cleanup_year(client, headers, 2099)

    created = client.post(
        "/api/v1/tarif-pajak/ptkp",
        headers=headers,
        json={"status_kode": "UT/0", "jumlah_ptkp": 1_000_000, "tahun_pajak": 2099},
    )
    assert created.status_code == 201
    ptkp_id = created.json()["id"]

    updated = client.patch(
        f"/api/v1/tarif-pajak/ptkp/{ptkp_id}",
        headers=headers,
        json={"jumlah_ptkp": 2_000_000},
    )
    assert updated.status_code == 200
    assert updated.json()["jumlah_ptkp"] == 2_000_000

    deleted = client.delete(f"/api/v1/tarif-pajak/ptkp/{ptkp_id}", headers=headers)
    assert deleted.status_code == 204


def test_non_superadmin_cannot_create_ptkp(client: TestClient) -> None:
    headers = auth_headers(client, ADMIN_A)
    resp = client.post(
        "/api/v1/tarif-pajak/ptkp",
        headers=headers,
        json={"status_kode": "UT/1", "jumlah_ptkp": 1_000_000, "tahun_pajak": 2099},
    )
    assert resp.status_code == 403


def test_authenticated_user_can_read_seeded_ptkp(client: TestClient) -> None:
    headers = auth_headers(client, ADMIN_A)
    resp = client.get("/api/v1/tarif-pajak/ptkp?tahun_pajak=2026", headers=headers)
    assert resp.status_code == 200
    assert any(item["status_kode"] == "TK/0" for item in resp.json())


def test_duplicate_ptkp_is_rejected(client: TestClient) -> None:
    headers = auth_headers(client, SUPERADMIN)
    resp = client.post(
        "/api/v1/tarif-pajak/ptkp",
        headers=headers,
        json={"status_kode": "TK/0", "jumlah_ptkp": 54_000_000, "tahun_pajak": 2026},
    )
    assert resp.status_code == 409


def test_superadmin_can_create_update_delete_progressive_bracket(client: TestClient) -> None:
    headers = auth_headers(client, SUPERADMIN)
    _cleanup_year(client, headers, 2099)

    created = client.post(
        "/api/v1/tarif-pajak/progresif",
        headers=headers,
        json={
            "batas_bawah": 0,
            "batas_atas": 10_000_000,
            "persentase_basis_points": 500,
            "tahun_pajak": 2099,
        },
    )
    assert created.status_code == 201
    bracket_id = created.json()["id"]

    updated = client.patch(
        f"/api/v1/tarif-pajak/progresif/{bracket_id}",
        headers=headers,
        json={"persentase_basis_points": 600},
    )
    assert updated.status_code == 200
    assert updated.json()["persentase_basis_points"] == 600

    deleted = client.delete(f"/api/v1/tarif-pajak/progresif/{bracket_id}", headers=headers)
    assert deleted.status_code == 204


def test_overlapping_progressive_bracket_is_rejected(client: TestClient) -> None:
    headers = auth_headers(client, SUPERADMIN)
    resp = client.post(
        "/api/v1/tarif-pajak/progresif",
        headers=headers,
        json={
            "batas_bawah": 1_000_000,
            "batas_atas": 2_000_000,
            "persentase_basis_points": 500,
            "tahun_pajak": 2026,
        },
    )
    assert resp.status_code == 409
