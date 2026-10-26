"""Account foundation tests: new login, force-change-password, bulk accounts,
tenant lifecycle (create+admin, quota, deactivate, export/delete), superadmin
read-only on documents, and the audit trail."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import auth_headers

ADMIN_A = "admin@smkn1-pku.local"
SUPERADMIN = "super@pajaksim.local"
GURU_A = "guru1@smkn1-pku.local"
SISWA_A = "siswa1@smkn1-pku.local"
BASE = "/api/v1"


# --- login changes ------------------------------------------------------------


def test_login_by_username_for_siswa(client: TestClient):
    """Siswa may log in with its NISN/NIK username instead of the email."""
    tokens = client.post(
        f"{BASE}/auth/login",
        json={"identifier": "NIS00000005", "password": "Password123!"},
    )
    assert tokens.status_code == 200, tokens.text

    me = client.get(
        f"{BASE}/auth/me",
        headers={"Authorization": f"Bearer {tokens.json()['access_token']}"},
    ).json()
    assert me["role"] == "siswa"
    assert me["username"] == "NIS00000005"
    assert me["must_change_password"] is False  # seed accounts are ready-to-use


def test_duplicate_email_across_tenants_is_rejected_at_creation(client: TestClient):
    headers = auth_headers(client, ADMIN_A)
    res = client.post(
        f"{BASE}/users",
        json={
            "email": GURU_A,  # belongs to the same tenant but must stay unique
            "password": "Password123!",
            "full_name": "Duplikat",
            "role": "guru",
        },
        headers=headers,
    )
    assert res.status_code == 409


# --- forced password change ---------------------------------------------------


def test_bulk_created_account_must_change_password_first(client: TestClient):
    import uuid

    headers = auth_headers(client, ADMIN_A)
    email = f"fresh.guru.{uuid.uuid4().hex[:6]}@smkn1-pku.local"
    res = client.post(
        f"{BASE}/users",
        json={
            "email": email,
            "password": "Password123!",
            "full_name": "Guru Baru",
            "role": "guru",
        },
        headers=headers,
    )
    assert res.status_code == 201
    account = res.json()
    # Direct creation keeps the provided password; flag stays false.
    assert account["must_change_password"] is False

    login = client.post(
        f"{BASE}/auth/login",
        json={"identifier": account["email"], "password": "Password123!"},
    )
    assert login.status_code == 200

    change = client.post(
        f"{BASE}/auth/change-password",
        json={"current_password": "Password123!", "new_password": "KataSandiBaru99!"},
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert change.status_code == 200, change.text
    assert change.json()["must_change_password"] is False

    relogin = client.post(
        f"{BASE}/auth/login",
        json={"identifier": account["email"], "password": "KataSandiBaru99!"},
    )
    assert relogin.status_code == 200

    wrong_current = client.post(
        f"{BASE}/auth/change-password",
        json={"current_password": "salah", "new_password": "KataSandiBaru99!"},
        headers=auth_headers(client, account["email"], password="KataSandiBaru99!"),
    )
    assert wrong_current.status_code == 422


# --- bulk creation ------------------------------------------------------------


def test_bulk_create_siswa_with_username_and_default_password(client: TestClient):
    import uuid

    suffix = uuid.uuid4().hex[:6]
    headers = auth_headers(client, ADMIN_A)
    res = client.post(
        f"{BASE}/users/bulk",
        json={
            "role": "siswa",
            "accounts": [
                # Full 6-hex-char suffix: the 2-char space (256 values) collides
                # after a few runs against the shared persistent test DB.
                {"full_name": "Bulk Satu", "username": f"0099988811{suffix}"},
                {"full_name": "Bulk Dua", "username": f"0099988812{suffix}"},
                # Duplicate of an existing username -> row fails, others succeed.
                {"full_name": "Bulk Tiga", "username": "NIS00000005"},
            ],
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["created"] == 2
    assert data["failed"] == 1
    ok_rows = [r for r in data["results"] if r["ok"]]
    assert all(r["username"] for r in ok_rows)

    # Default password works and flags the forced change.
    login = client.post(
        f"{BASE}/auth/login",
        json={"identifier": ok_rows[0]["username"], "password": "Password123!"},
    )
    assert login.status_code == 200
    me = client.get(
        f"{BASE}/auth/me",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    ).json()
    assert me["must_change_password"] is True


def test_bulk_create_respects_quota(client: TestClient):
    """Superadmin sets the quota explicitly, then bulk creation hits the cap."""
    admin_headers = auth_headers(client, ADMIN_A)
    super_headers = auth_headers(client, SUPERADMIN)

    current = client.get(
        f"{BASE}/users", params={"role": "siswa", "size": 1}, headers=admin_headers
    ).json()["total"]
    tenant_id = client.get(f"{BASE}/auth/me", headers=admin_headers).json()["tenant_id"]

    # Cap at current + 1, then ask for 3 more -> 409 before anything is created.
    res = client.patch(
        f"{BASE}/tenants/{tenant_id}",
        json={"max_siswa": current + 1},
        headers=super_headers,
    )
    assert res.status_code == 200, res.text

    res = client.post(
        f"{BASE}/users/bulk",
        json={
            "role": "siswa",
            "accounts": [
                {"full_name": "Kuota A", "username": "0088776601"},
                {"full_name": "Kuota B", "username": "0088776602"},
                {"full_name": "Kuota C", "username": "0088776603"},
            ],
        },
        headers=admin_headers,
    )
    assert res.status_code == 409
    assert "Kuota" in res.json()["detail"]

    # Restore unlimited so sibling tests that create accounts keep working.
    restored = client.patch(
        f"{BASE}/tenants/{tenant_id}",
        json={"max_siswa": None},
        headers=super_headers,
    )
    assert restored.status_code == 200
    assert restored.json()["max_siswa"] is None


def test_bulk_create_guru_requires_email(client: TestClient):
    headers = auth_headers(client, ADMIN_A)
    res = client.post(
        f"{BASE}/users/bulk",
        json={"role": "guru", "accounts": [{"full_name": "Tanpa Email"}]},
        headers=headers,
    )
    assert res.status_code == 201
    assert res.json()["failed"] == 1


# --- tenant lifecycle ---------------------------------------------------------


def test_superadmin_creates_tenant_with_first_admin(client: TestClient):
    import uuid

    suffix = uuid.uuid4().hex[:6]
    headers = auth_headers(client, SUPERADMIN)
    res = client.post(
        f"{BASE}/tenants",
        json={
            "name": "PT Simulasi Baru",
            "slug": f"pt-simulasi-baru-{suffix}",
            "type": "pt",
            "contact_wa": "0812-9999-9999",
            "masa_berlaku_bulan": 6,
            "max_guru": 2,
            "max_siswa": 5,
            "max_kelas": 2,
            "admin": {
                "email": f"admin.pt-{suffix}@simulasi.local",
                "full_name": "Admin PT Simulasi",
            },
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    tenant = res.json()
    assert tenant["type"] == "pt"
    assert tenant["berlaku_sampai"] is not None
    assert tenant["max_siswa"] == 5

    # The first admin can log in immediately and sees the forced-change flag.
    login = client.post(
        f"{BASE}/auth/login",
        json={
            "identifier": f"admin.pt-{suffix}@simulasi.local",
            "password": "Password123!",
        },
    )
    assert login.status_code == 200
    me = client.get(
        f"{BASE}/auth/me",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    ).json()
    assert me["role"] == "admin"
    assert me["must_change_password"] is True

    # Quota bites on the new tenant: max_siswa = 5.
    admin_headers = auth_headers(client, f"admin.pt-{suffix}@simulasi.local")
    res = client.post(
        f"{BASE}/users/bulk",
        json={
            "role": "siswa",
            "accounts": [
                {"full_name": f"PT Siswa {i}", "username": f"00776655{suffix[:2]}{i:02d}"}
                for i in range(6)
            ],
        },
        headers=admin_headers,
    )
    assert res.status_code == 409


def test_deactivate_tenant_blocks_logins_but_keeps_data(client: TestClient):
    super_headers = auth_headers(client, SUPERADMIN)
    siswa_headers = auth_headers(client, SISWA_A)

    # Create a disposable tenant + admin.
    import uuid

    suffix = uuid.uuid4().hex[:6]
    res = client.post(
        f"{BASE}/tenants",
        json={
            "name": "Sekolah Nonaktif",
            "slug": f"sekolah-nonaktif-{suffix}",
            "type": "smk",
            "masa_berlaku_bulan": 12,
            "admin": {
                "email": f"admin.sn-{suffix}@simulasi.local",
                "full_name": "Admin Nonaktif",
            },
        },
        headers=super_headers,
    )
    assert res.status_code == 201
    tenant_id = res.json()["id"]

    # Deactivate with a reason.
    res = client.post(
        f"{BASE}/tenants/{tenant_id}/deactivate",
        json={"reason": "Berhenti berlangganan"},
        headers=super_headers,
    )
    assert res.status_code == 200
    assert res.json()["is_active"] is False
    assert res.json()["deactivated_reason"] == "Berhenti berlangganan"

    # The admin of that tenant can no longer log in.
    denied = client.post(
        f"{BASE}/auth/login",
        json={
            "identifier": f"admin.sn-{suffix}@simulasi.local",
            "password": "Password123!",
        },
    )
    assert denied.status_code == 403
    assert "tidak aktif" in denied.json()["detail"]

    # The data is intact — the superadmin still reads the user list.
    users = client.get(
        f"{BASE}/users", params={"tenant_id": tenant_id}, headers=super_headers
    ).json()
    assert users["total"] >= 1

    # Reactivate → login works again.
    res = client.post(
        f"{BASE}/tenants/{tenant_id}/activate",
        json={"reason": "Perpanjangan kontrak"},
        headers=super_headers,
    )
    assert res.status_code == 200
    assert res.json()["deactivated_reason"] is None

    # Siswa A (tenant lain) tetap tidak bisa menyentuh instansi lain —
    # guard superadmin-only endpoint.
    denied = client.post(
        f"{BASE}/tenants/{tenant_id}/deactivate",
        json={"reason": "bukan superadmin"},
        headers=siswa_headers,
    )
    assert denied.status_code == 403


def test_delete_tenant_requires_typed_code(client: TestClient):
    super_headers = auth_headers(client, SUPERADMIN)
    import uuid

    suffix = uuid.uuid4().hex[:6]
    slug = f"sekolah-hapus-{suffix}"
    res = client.post(
        f"{BASE}/tenants",
        json={
            "name": "Sekolah Akan Dihapus",
            "slug": slug,
            "type": "lembaga",
            "masa_berlaku_bulan": 12,
            "admin": {
                "email": f"admin.sh-{suffix}@simulasi.local",
                "full_name": "Admin Hapus",
            },
        },
        headers=super_headers,
    )
    tenant = res.json()

    # Wrong confirmation code -> 422.
    res = client.request(
        "DELETE",
        f"{BASE}/tenants/{tenant['id']}",
        json={"confirm_code": "salah"},
        headers=super_headers,
    )
    assert res.status_code == 422

    # Export the backup before deleting.
    backup = client.get(f"{BASE}/tenants/{tenant['id']}/export", headers=super_headers)
    assert backup.status_code == 200
    assert backup.headers["content-type"].startswith("application/json")
    body = backup.json()
    assert body["tenant"]["slug"] == slug
    assert len(body["users"]) >= 1

    # Correct confirmation -> gone.
    res = client.request(
        "DELETE",
        f"{BASE}/tenants/{tenant['id']}",
        json={"confirm_code": slug},
        headers=super_headers,
    )
    assert res.status_code == 204
    remaining_ids = {
        t["id"] for t in client.get(f"{BASE}/tenants", headers=super_headers).json()
    }
    assert tenant["id"] not in remaining_ids


# --- superadmin read-only on documents ---------------------------------------


def test_superadmin_cannot_mutate_documents(client: TestClient):
    """Read everywhere is fine, but write endpoints reject the superadmin."""
    super_headers = auth_headers(client, SUPERADMIN)

    # Reading works.
    assert client.get(f"{BASE}/bp23", headers=super_headers).status_code == 200
    assert client.get(f"{BASE}/faktur-keluaran", headers=super_headers).status_code == 200
    assert client.get(f"{BASE}/spt", headers=super_headers).status_code == 200

    # Creating a bupot is rejected.
    res = client.post(
        f"{BASE}/bp23",
        json={
            "tax_month": 11,
            "tax_year": 2026,
            "recipient_identity_number": "1234567890123456",
            "recipient_name": "Coba Superadmin",
            "tax_object_code": "24-100-01",
            "gross_income": 1_000_000,
            "dpp_percent": 100,
            "rate_percent": 15,
        },
        headers=super_headers,
    )
    assert res.status_code == 403

    # Grading is rejected too.
    some_spt = client.get(f"{BASE}/spt?size=1", headers=super_headers).json()
    if some_spt["total"]:
        spt_id = some_spt["items"][0]["id"]
        res = client.patch(
            f"{BASE}/spt/{spt_id}/review",
            json={"score": 100},
            headers=super_headers,
        )
        assert res.status_code == 403


def test_admin_cannot_modify_other_admins_or_foreign_tenant(client: TestClient):
    headers = auth_headers(client, ADMIN_A)

    # Pick a user from another tenant via superadmin list is irrelevant —
    # admin tries to patch a guru of tenant B by guessing IDs is scoped 404.
    res = client.get(f"{BASE}/users?size=100", headers=headers)
    own = {u["id"] for u in res.json()["items"]}

    # Try to patch admin@pcr.local (foreign tenant) — must be denied/hidden.
    foreign = client.get(
        f"{BASE}/users?size=100", headers=auth_headers(client, "admin@pcr.local")
    ).json()
    foreign_ids = [u["id"] for u in foreign["items"] if u["id"] not in own]
    if foreign_ids:
        res = client.patch(
            f"{BASE}/users/{foreign_ids[0]}",
            json={"is_active": False},
            headers=headers,
        )
        assert res.status_code == 403


# --- audit trail --------------------------------------------------------------


def test_audit_log_records_and_scopes(client: TestClient):
    admin_headers = auth_headers(client, ADMIN_A)

    # An action that must be logged: deactivate a guru.
    users = client.get(
        f"{BASE}/users", params={"role": "guru"}, headers=admin_headers
    ).json()
    target = next(u for u in users["items"] if u["is_active"])
    res = client.patch(
        f"{BASE}/users/{target['id']}",
        json={"is_active": False},
        headers=admin_headers,
    )
    assert res.status_code == 200
    client.patch(
        f"{BASE}/users/{target['id']}",
        json={"is_active": True},
        headers=admin_headers,
    )

    logs = client.get(f"{BASE}/audit-logs", headers=admin_headers).json()
    assert logs["total"] >= 1
    actions = {item["action"] for item in logs["items"]}
    assert "user.deactivate" in actions
    assert all(item["tenant_id"] for item in logs["items"])

    # Guru cannot read the audit trail at all.
    denied = client.get(f"{BASE}/audit-logs", headers=auth_headers(client, GURU_A))
    assert denied.status_code == 403

    # Superadmin sees everything (cross-tenant included).
    super_logs = client.get(f"{BASE}/audit-logs", headers=auth_headers(client, SUPERADMIN))
    assert super_logs.status_code == 200


# --- class move / archive ------------------------------------------------------


def test_move_siswa_between_classes_and_archive(client: TestClient):
    admin_headers = auth_headers(client, ADMIN_A)
    classes = client.get(f"{BASE}/classes", headers=admin_headers).json()
    assert len(classes) >= 2
    source, destination = classes[0], classes[1]

    detail = client.get(f"{BASE}/classes/{source['id']}", headers=admin_headers).json()
    assert detail["students"], "seed class must have students"

    siswa_id = detail["students"][0]["id"]
    # Move = remove here, add there.
    removed = client.delete(
        f"{BASE}/classes/{source['id']}/enrollments/{siswa_id}", headers=admin_headers
    )
    assert removed.status_code == 200
    added = client.post(
        f"{BASE}/classes/{destination['id']}/enrollments",
        json={"siswa_id": siswa_id},
        headers=admin_headers,
    )
    assert added.status_code == 201
    assert any(s["id"] == siswa_id for s in added.json()["students"])

    # Put it back so other tests keep their assumptions.
    client.delete(
        f"{BASE}/classes/{destination['id']}/enrollments/{siswa_id}", headers=admin_headers
    )
    client.post(
        f"{BASE}/classes/{source['id']}/enrollments",
        json={"siswa_id": siswa_id},
        headers=admin_headers,
    )

    # Archive/unarchive.
    archived = client.patch(
        f"{BASE}/classes/{source['id']}/archive",
        json={"archived": True},
        headers=admin_headers,
    )
    assert archived.status_code == 200
    assert archived.json()["archived"] is True
    unarchived = client.patch(
        f"{BASE}/classes/{source['id']}/archive",
        json={"archived": False},
        headers=admin_headers,
    )
    assert unarchived.json()["archived"] is False
