"""Regression: superadmin read-only cross-tenant access to classes."""

from fastapi.testclient import TestClient

from tests.conftest import auth_headers

BASE = "/api/v1"
SUPERADMIN = "super@pajaksim.local"
ADMIN = "admin@smkn1-pku.local"


def test_superadmin_can_list_classes_cross_tenant(client: TestClient):
    super_headers = auth_headers(client, SUPERADMIN)
    res = client.get(f"{BASE}/classes", headers=super_headers)
    assert res.status_code == 200, res.text
    items = res.json()
    assert isinstance(items, list) and items, "seed must provide classes"
    tenant_ids = {c["tenant_id"] for c in items}
    assert len(tenant_ids) >= 1


def test_superadmin_can_read_class_detail_but_cannot_write(client: TestClient):
    super_headers = auth_headers(client, SUPERADMIN)
    admin_headers = auth_headers(client, ADMIN)

    classes = client.get(f"{BASE}/classes", headers=admin_headers).json()
    assert classes
    class_id = classes[0]["id"]

    detail = client.get(f"{BASE}/classes/{class_id}", headers=super_headers)
    assert detail.status_code == 200, detail.text

    # Write stays admin-only.
    denied_create = client.post(
        f"{BASE}/classes",
        json={"name": "X", "academic_year": "2026/2027", "guru_id": classes[0]["guru_id"]},
        headers=super_headers,
    )
    assert denied_create.status_code == 403

    denied_enroll = client.post(
        f"{BASE}/classes/{class_id}/enrollments",
        json={"siswa_id": 1},
        headers=super_headers,
    )
    assert denied_enroll.status_code == 403

    denied_archive = client.patch(
        f"{BASE}/classes/{class_id}/archive",
        json={"archived": True},
        headers=super_headers,
    )
    assert denied_archive.status_code == 403
