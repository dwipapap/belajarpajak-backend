"""Comprehensive tests for Admin & Superadmin bug fixes and QoL overhaul."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.core.rate_limit import login_limiter
from app.db import engine
from app.models.assignment import Assignment, AssignmentSubmission
from app.models.billing import BillingCode
from app.models.enums import (
    AssignmentDocType,
    AssignmentStatus,
    BillingStatus,
    SlipStatus,
    SlipTaxNature,
    SlipType,
    TaxReturnStatus,
    TaxReturnType,
)
from app.models.slip import WithholdingSlip
from app.models.tax_return import TaxReturn
from tests.conftest import auth_headers

BASE = "/api/v1"
SUPERADMIN = "super@pajaksim.local"
ADMIN_A = "admin@smkn1-pku.local"


def _create_tenant(
    client: TestClient,
    super_headers: dict[str, str],
    *,
    max_guru: int | None = None,
    max_siswa: int | None = None,
    max_kelas: int | None = None,
) -> tuple[dict, str, dict[str, str]]:
    suffix = uuid.uuid4().hex[:8]
    admin_email = f"admin.qol.{suffix}@simulasi.local"
    res = client.post(
        f"{BASE}/tenants",
        json={
            "name": f"Sekolah QoL {suffix}",
            "slug": f"qol-{suffix}",
            "type": "smk",
            "masa_berlaku_bulan": 12,
            "max_guru": max_guru,
            "max_siswa": max_siswa,
            "max_kelas": max_kelas,
            "admin": {
                "email": admin_email,
                "full_name": f"Admin QoL {suffix}",
            },
        },
        headers=super_headers,
    )
    assert res.status_code == 201, res.text
    tenant = res.json()
    return tenant, admin_email, auth_headers(client, admin_email)


def test_superadmin_create_admin_and_siswa_without_email(client: TestClient):
    super_headers = auth_headers(client, SUPERADMIN)
    tenant, _, admin_headers = _create_tenant(client, super_headers)
    suffix = uuid.uuid4().hex[:6]

    # Superadmin can create an additional admin in target tenant.
    extra_admin_email = f"admin2.{suffix}@simulasi.local"
    res = client.post(
        f"{BASE}/users",
        json={
            "email": extra_admin_email,
            "password": "Password123!",
            "full_name": "Admin Kedua",
            "role": "admin",
            "tenant_id": tenant["id"],
        },
        headers=super_headers,
    )
    assert res.status_code == 201, res.text
    created_admin = res.json()
    assert created_admin["role"] == "admin"
    assert created_admin["tenant_id"] == tenant["id"]

    # Superadmin cannot create another superadmin via /users.
    denied_super = client.post(
        f"{BASE}/users",
        json={
            "email": f"super2.{suffix}@simulasi.local",
            "password": "Password123!",
            "full_name": "Nope",
            "role": "superadmin",
            "tenant_id": tenant["id"],
        },
        headers=super_headers,
    )
    assert denied_super.status_code == 403

    # Admin still cannot create an admin account.
    denied_admin = client.post(
        f"{BASE}/users",
        json={
            "email": f"admin3.{suffix}@simulasi.local",
            "password": "Password123!",
            "full_name": "Nope Admin",
            "role": "admin",
        },
        headers=admin_headers,
    )
    assert denied_admin.status_code == 403

    # Non-siswa still requires email (422 if omitted).
    missing_guru_email = client.post(
        f"{BASE}/users",
        json={
            "password": "Password123!",
            "full_name": "Guru Tanpa Email",
            "role": "guru",
        },
        headers=admin_headers,
    )
    assert missing_guru_email.status_code == 422

    # Siswa single create without email auto-generates placeholder email.
    nis = f"77{suffix}"
    res_siswa = client.post(
        f"{BASE}/users",
        json={
            "password": "Password123!",
            "full_name": "Siswa Tanpa Email",
            "role": "siswa",
            "username": nis,
        },
        headers=admin_headers,
    )
    assert res_siswa.status_code == 201, res_siswa.text
    siswa = res_siswa.json()
    assert siswa["email"] == f"{nis}@siswa.pajaksim.local"
    assert siswa["username"] == nis

    # Siswa can log in using the username.
    login_res = client.post(
        f"{BASE}/auth/login",
        json={"identifier": nis, "password": "Password123!"},
    )
    assert login_res.status_code == 200, login_res.text


def test_list_users_search_q_and_class_names(client: TestClient):
    super_headers = auth_headers(client, SUPERADMIN)
    tenant, _, admin_headers = _create_tenant(client, super_headers)
    suffix = uuid.uuid4().hex[:6]

    guru = client.post(
        f"{BASE}/users",
        json={
            "email": f"guru.search.{suffix}@simulasi.local",
            "password": "Password123!",
            "full_name": f"Budi Pengajar {suffix}",
            "role": "guru",
        },
        headers=admin_headers,
    ).json()

    active_cls = client.post(
        f"{BASE}/classes",
        json={
            "name": f"XI Akuntansi {suffix}",
            "academic_year": "2026/2027",
            "guru_id": guru["id"],
        },
        headers=admin_headers,
    ).json()
    archived_cls = client.post(
        f"{BASE}/classes",
        json={
            "name": f"X Lama {suffix}",
            "academic_year": "2025/2026",
            "guru_id": guru["id"],
        },
        headers=admin_headers,
    ).json()
    client.patch(
        f"{BASE}/classes/{archived_cls['id']}/archive",
        json={"archived": True},
        headers=admin_headers,
    )

    nis = f"88{suffix}"
    siswa = client.post(
        f"{BASE}/users",
        json={
            "password": "Password123!",
            "full_name": f"Siti Pencarian {suffix}",
            "role": "siswa",
            "username": nis,
        },
        headers=admin_headers,
    ).json()

    client.post(
        f"{BASE}/classes/{active_cls['id']}/enrollments",
        json={"siswa_id": siswa["id"]},
        headers=admin_headers,
    )
    client.post(
        f"{BASE}/classes/{archived_cls['id']}/enrollments",
        json={"siswa_id": siswa["id"]},
        headers=admin_headers,
    )

    # Search by substring in full_name (case-insensitive)
    by_name = client.get(
        f"{BASE}/users",
        params={"q": f"siti pencarian {suffix.upper()}"},
        headers=admin_headers,
    )
    assert by_name.status_code == 200
    items = by_name.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == siswa["id"]
    # Only non-archived class appears in class_names
    assert items[0]["class_names"] == [active_cls["name"]]

    # Search by username substring
    by_nis = client.get(
        f"{BASE}/users", params={"q": nis}, headers=admin_headers
    ).json()
    assert by_nis["total"] == 1 and by_nis["items"][0]["id"] == siswa["id"]

    # Search by email substring
    by_email = client.get(
        f"{BASE}/users", params={"q": f"guru.search.{suffix}"}, headers=admin_headers
    ).json()
    assert by_email["total"] == 1 and by_email["items"][0]["id"] == guru["id"]


def test_bulk_create_with_class_id_default_password_and_must_change(client: TestClient):
    super_headers = auth_headers(client, SUPERADMIN)
    tenant, _, admin_headers = _create_tenant(client, super_headers)
    other_tenant, _, other_admin_headers = _create_tenant(client, super_headers)
    suffix = uuid.uuid4().hex[:6]

    guru = client.post(
        f"{BASE}/users",
        json={
            "email": f"guru.bulk.{suffix}@simulasi.local",
            "password": "Password123!",
            "full_name": "Guru Bulk",
            "role": "guru",
        },
        headers=admin_headers,
    ).json()
    cls = client.post(
        f"{BASE}/classes",
        json={"name": f"XII Bulk {suffix}", "academic_year": "2026/2027", "guru_id": guru["id"]},
        headers=admin_headers,
    ).json()

    other_guru = client.post(
        f"{BASE}/users",
        json={
            "email": f"other.guru.{suffix}@simulasi.local",
            "password": "Password123!",
            "full_name": "Other Guru",
            "role": "guru",
        },
        headers=other_admin_headers,
    ).json()
    other_cls = client.post(
        f"{BASE}/classes",
        json={"name": f"Other {suffix}", "academic_year": "2026/2027", "guru_id": other_guru["id"]},
        headers=other_admin_headers,
    ).json()

    # Foreign class_id -> 422
    bad_class = client.post(
        f"{BASE}/users/bulk",
        json={
            "role": "siswa",
            "class_id": other_cls["id"],
            "accounts": [{"full_name": "Siswa X", "username": f"66{suffix}1"}],
        },
        headers=admin_headers,
    )
    assert bad_class.status_code == 422

    # Valid class_id + default_password + explicit must_change_password=False
    u1, u2 = f"66{suffix}01", f"66{suffix}02"
    res = client.post(
        f"{BASE}/users/bulk",
        json={
            "role": "siswa",
            "class_id": cls["id"],
            "default_password": "CustomDefault99!",
            "must_change_password": False,
            "accounts": [
                {"full_name": "Bulk Class Satu", "username": u1},
                {"full_name": "Bulk Class Dua", "username": u2, "password": "RowPassword99!"},
            ],
        },
        headers=admin_headers,
    )
    assert res.status_code == 201, res.text
    assert res.json()["created"] == 2

    # Both enrolled in cls
    detail = client.get(f"{BASE}/classes/{cls['id']}", headers=admin_headers).json()
    assert detail["student_count"] == 2
    assert {s["username"] for s in detail["students"]} == {u1, u2}

    # u1 uses default_password and respects must_change_password=False
    login1 = client.post(
        f"{BASE}/auth/login", json={"identifier": u1, "password": "CustomDefault99!"}
    )
    assert login1.status_code == 200
    me1 = client.get(
        f"{BASE}/auth/me", headers={"Authorization": f"Bearer {login1.json()['access_token']}"}
    ).json()
    assert me1["must_change_password"] is False

    # u2 uses per-row password
    login2 = client.post(
        f"{BASE}/auth/login", json={"identifier": u2, "password": "RowPassword99!"}
    )
    assert login2.status_code == 200


def test_patch_user_profile_and_password_reset_clears_limiter(client: TestClient):
    super_headers = auth_headers(client, SUPERADMIN)
    _, _, admin_headers = _create_tenant(client, super_headers)
    suffix = uuid.uuid4().hex[:6]

    nis1 = f"55{suffix}01"
    nis2 = f"55{suffix}02"
    s1 = client.post(
        f"{BASE}/users",
        json={
            "password": "Password123!",
            "full_name": "Siswa Edit 1",
            "role": "siswa",
            "username": nis1,
        },
        headers=admin_headers,
    ).json()
    client.post(
        f"{BASE}/users",
        json={
            "password": "Password123!",
            "full_name": "Siswa Edit 2",
            "role": "siswa",
            "username": nis2,
        },
        headers=admin_headers,
    )
    g1 = client.post(
        f"{BASE}/users",
        json={
            "email": f"guru.edit.{suffix}@simulasi.local",
            "password": "Password123!",
            "full_name": "Guru Edit",
            "role": "guru",
        },
        headers=admin_headers,
    ).json()

    # Duplicate email -> 409
    dup_email = client.patch(
        f"{BASE}/users/{s1['id']}",
        json={"email": g1["email"].upper()},
        headers=admin_headers,
    )
    assert dup_email.status_code == 409

    # Duplicate username -> 409
    dup_user = client.patch(
        f"{BASE}/users/{s1['id']}",
        json={"username": nis2},
        headers=admin_headers,
    )
    assert dup_user.status_code == 409

    # Valid email (normalized lowercase) + username update for siswa
    new_email = f"Siswa.Updated.{suffix}@Simulasi.Local"
    new_nis = f"55{suffix}99"
    upd = client.patch(
        f"{BASE}/users/{s1['id']}",
        json={"email": new_email, "username": new_nis, "full_name": "Siswa Updated"},
        headers=admin_headers,
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["email"] == new_email.lower()
    assert upd.json()["username"] == new_nis
    assert upd.json()["full_name"] == "Siswa Updated"

    # Username update on non-siswa is ignored
    upd_guru = client.patch(
        f"{BASE}/users/{g1['id']}",
        json={"username": "IGNORED123", "full_name": "Guru Updated"},
        headers=admin_headers,
    )
    assert upd_guru.status_code == 200
    assert upd_guru.json()["username"] is None
    assert upd_guru.json()["full_name"] == "Guru Updated"

    # Lock out s1 via failed logins on both email and username (IP limiter is not triggered
    # because LOGIN_MAX_FAILURES_PER_IP is much larger than LOGIN_MAX_FAILURES_PER_ACCOUNT)
    for _ in range(settings.LOGIN_MAX_FAILURES_PER_ACCOUNT):
        r1 = client.post(
            f"{BASE}/auth/login", json={"identifier": new_email.lower(), "password": "wrong"}
        )
        assert r1.status_code == 401
        r2 = client.post(
            f"{BASE}/auth/login", json={"identifier": new_nis, "password": "wrong"}
        )
        assert r2.status_code == 401

    locked_email = client.post(
        f"{BASE}/auth/login", json={"identifier": new_email.lower(), "password": "Password123!"}
    )
    assert locked_email.status_code == 429
    locked_nis = client.post(
        f"{BASE}/auth/login", json={"identifier": new_nis, "password": "Password123!"}
    )
    assert locked_nis.status_code == 429

    # Password reset clears login_limiter for both email and username
    reset_res = client.patch(
        f"{BASE}/users/{s1['id']}",
        json={"password": "ResetPassword99!"},
        headers=admin_headers,
    )
    assert reset_res.status_code == 200

    ok_email = client.post(
        f"{BASE}/auth/login", json={"identifier": new_email.lower(), "password": "ResetPassword99!"}
    )
    assert ok_email.status_code == 200
    login_limiter.clear()  # reset IP counter too before testing username login
    # Re-verify resetting username specifically clears username bucket:
    for _ in range(settings.LOGIN_MAX_FAILURES_PER_ACCOUNT):
        client.post(f"{BASE}/auth/login", json={"identifier": new_nis, "password": "wrong"})
    assert client.post(
        f"{BASE}/auth/login", json={"identifier": new_nis, "password": "ResetPassword99!"}
    ).status_code == 429
    client.patch(
        f"{BASE}/users/{s1['id']}",
        json={"password": "ResetPassword88!"},
        headers=admin_headers,
    )
    ok_nis = client.post(
        f"{BASE}/auth/login", json={"identifier": new_nis, "password": "ResetPassword88!"}
    )
    assert ok_nis.status_code == 200


def test_superadmin_class_crud_bulk_enroll_and_student_count(client: TestClient):
    super_headers = auth_headers(client, SUPERADMIN)
    tenant, _, admin_headers = _create_tenant(client, super_headers, max_kelas=2)
    other_tenant, _, other_admin_headers = _create_tenant(client, super_headers)
    suffix = uuid.uuid4().hex[:6]

    guru1 = client.post(
        f"{BASE}/users",
        json={
            "email": f"g1.cls.{suffix}@simulasi.local",
            "password": "Password123!",
            "full_name": "Guru Satu",
            "role": "guru",
        },
        headers=admin_headers,
    ).json()
    guru2 = client.post(
        f"{BASE}/users",
        json={
            "email": f"g2.cls.{suffix}@simulasi.local",
            "password": "Password123!",
            "full_name": "Guru Dua",
            "role": "guru",
        },
        headers=admin_headers,
    ).json()
    other_guru = client.post(
        f"{BASE}/users",
        json={
            "email": f"go.cls.{suffix}@simulasi.local",
            "password": "Password123!",
            "full_name": "Guru Lain",
            "role": "guru",
        },
        headers=other_admin_headers,
    ).json()

    # Superadmin creates class with explicit tenant_id
    c1_res = client.post(
        f"{BASE}/classes",
        json={
            "name": f"XII Super {suffix}",
            "academic_year": "2026/2027",
            "guru_id": guru1["id"],
            "tenant_id": tenant["id"],
        },
        headers=super_headers,
    )
    assert c1_res.status_code == 201, c1_res.text
    c1 = c1_res.json()
    assert c1["tenant_id"] == tenant["id"]
    assert c1["student_count"] == 0

    # Superadmin creates class omitting tenant_id -> resolved from guru.tenant_id
    c2_res = client.post(
        f"{BASE}/classes",
        json={
            "name": f"XI Super {suffix}",
            "academic_year": "2026/2027",
            "guru_id": guru1["id"],
        },
        headers=super_headers,
    )
    assert c2_res.status_code == 201, c2_res.text
    assert c2_res.json()["tenant_id"] == tenant["id"]

    # Quota max_kelas=2 is enforced for superadmin too -> 409 on 3rd class
    c3_res = client.post(
        f"{BASE}/classes",
        json={
            "name": f"X Over Quota {suffix}",
            "academic_year": "2026/2027",
            "guru_id": guru1["id"],
        },
        headers=super_headers,
    )
    assert c3_res.status_code == 409

    # Mismatched guru/tenant_id -> 422
    mismatch = client.post(
        f"{BASE}/classes",
        json={
            "name": "Mismatch",
            "academic_year": "2026/2027",
            "guru_id": other_guru["id"],
            "tenant_id": tenant["id"],
        },
        headers=super_headers,
    )
    assert mismatch.status_code == 422

    # PATCH /classes/{id} update name, academic_year, guru_id
    upd = client.patch(
        f"{BASE}/classes/{c1['id']}",
        json={
            "name": f"XII Edited {suffix}",
            "academic_year": "2027/2028",
            "guru_id": guru2["id"],
        },
        headers=super_headers,
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["name"] == f"XII Edited {suffix}"
    assert upd.json()["academic_year"] == "2027/2028"
    assert upd.json()["guru_id"] == guru2["id"]

    # PATCH with foreign guru -> 422
    bad_upd = client.patch(
        f"{BASE}/classes/{c1['id']}",
        json={"guru_id": other_guru["id"]},
        headers=admin_headers,
    )
    assert bad_upd.status_code == 422

    # Bulk enroll siswa
    s1 = client.post(
        f"{BASE}/users",
        json={
            "password": "Password123!",
            "full_name": "S1",
            "role": "siswa",
            "username": f"44{suffix}1",
        },
        headers=admin_headers,
    ).json()
    s2 = client.post(
        f"{BASE}/users",
        json={
            "password": "Password123!",
            "full_name": "S2",
            "role": "siswa",
            "username": f"44{suffix}2",
        },
        headers=admin_headers,
    ).json()
    foreign_s = client.post(
        f"{BASE}/users",
        json={
            "password": "Password123!",
            "full_name": "SF",
            "role": "siswa",
            "username": f"44{suffix}9",
        },
        headers=other_admin_headers,
    ).json()

    # Reject if any ID is invalid/foreign/non-siswa
    bad_bulk = client.post(
        f"{BASE}/classes/{c1['id']}/enrollments/bulk",
        json={"siswa_ids": [s1["id"], foreign_s["id"]]},
        headers=super_headers,
    )
    assert bad_bulk.status_code == 422
    bad_role_bulk = client.post(
        f"{BASE}/classes/{c1['id']}/enrollments/bulk",
        json={"siswa_ids": [guru1["id"]]},
        headers=super_headers,
    )
    assert bad_role_bulk.status_code == 422

    # Pre-enroll s1, then bulk enroll [s1, s2, s2] -> idempotent, student_count == 2
    client.post(
        f"{BASE}/classes/{c1['id']}/enrollments",
        json={"siswa_id": s1["id"]},
        headers=super_headers,
    )
    bulk_ok = client.post(
        f"{BASE}/classes/{c1['id']}/enrollments/bulk",
        json={"siswa_ids": [s1["id"], s2["id"], s2["id"]]},
        headers=super_headers,
    )
    assert bulk_ok.status_code == 201, bulk_ok.text
    assert bulk_ok.json()["student_count"] == 2
    assert {s["id"] for s in bulk_ok.json()["students"]} == {s1["id"], s2["id"]}

    # Superadmin list_classes with tenant_id filter shows accurate student_count
    listed = client.get(
        f"{BASE}/classes", params={"tenant_id": tenant["id"]}, headers=super_headers
    )
    assert listed.status_code == 200
    by_id = {c["id"]: c for c in listed.json()}
    assert set(by_id.keys()) == {c1["id"], c2_res.json()["id"]}
    assert by_id[c1["id"]]["student_count"] == 2
    assert by_id[c2_res.json()["id"]]["student_count"] == 0

    # Superadmin unenroll and archive
    unenr = client.delete(
        f"{BASE}/classes/{c1['id']}/enrollments/{s2['id']}",
        headers=super_headers,
    )
    assert unenr.status_code == 200
    assert unenr.json()["student_count"] == 1

    arch = client.patch(
        f"{BASE}/classes/{c1['id']}/archive",
        json={"archived": True},
        headers=super_headers,
    )
    assert arch.status_code == 200
    assert arch.json()["archived"] is True
    assert arch.json()["student_count"] == 1


def test_dashboard_unenrolled_siswa(client: TestClient):
    super_headers = auth_headers(client, SUPERADMIN)
    tenant, _, admin_headers = _create_tenant(client, super_headers)
    suffix = uuid.uuid4().hex[:6]

    guru = client.post(
        f"{BASE}/users",
        json={
            "email": f"g.dash.{suffix}@simulasi.local",
            "password": "Password123!",
            "full_name": "Guru Dash",
            "role": "guru",
        },
        headers=admin_headers,
    ).json()
    active_cls = client.post(
        f"{BASE}/classes",
        json={"name": f"Aktif {suffix}", "academic_year": "2026/2027", "guru_id": guru["id"]},
        headers=admin_headers,
    ).json()
    archived_cls = client.post(
        f"{BASE}/classes",
        json={"name": f"Arsip {suffix}", "academic_year": "2025/2026", "guru_id": guru["id"]},
        headers=admin_headers,
    ).json()
    client.patch(
        f"{BASE}/classes/{archived_cls['id']}/archive",
        json={"archived": True},
        headers=admin_headers,
    )

    # s_enrolled -> enrolled in active_cls (should NOT count as unenrolled)
    # s_only_archived -> enrolled only in archived_cls (SHOULD count as unenrolled)
    # s_no_class -> active, no enrollment (SHOULD count as unenrolled)
    # s_inactive -> inactive, no enrollment (should NOT count as unenrolled)
    s_enrolled = client.post(
        f"{BASE}/users",
        json={
            "password": "Password123!",
            "full_name": "Enrolled",
            "role": "siswa",
            "username": f"33{suffix}1",
        },
        headers=admin_headers,
    ).json()
    s_only_archived = client.post(
        f"{BASE}/users",
        json={
            "password": "Password123!",
            "full_name": "ArchOnly",
            "role": "siswa",
            "username": f"33{suffix}2",
        },
        headers=admin_headers,
    ).json()
    client.post(
        f"{BASE}/users",
        json={
            "password": "Password123!",
            "full_name": "NoClass",
            "role": "siswa",
            "username": f"33{suffix}3",
        },
        headers=admin_headers,
    )
    s_inactive = client.post(
        f"{BASE}/users",
        json={
            "password": "Password123!",
            "full_name": "Inactive",
            "role": "siswa",
            "username": f"33{suffix}4",
        },
        headers=admin_headers,
    ).json()
    client.patch(
        f"{BASE}/users/{s_inactive['id']}",
        json={"is_active": False},
        headers=admin_headers,
    )
    client.post(
        f"{BASE}/classes/{active_cls['id']}/enrollments",
        json={"siswa_id": s_enrolled["id"]},
        headers=admin_headers,
    )
    client.post(
        f"{BASE}/classes/{archived_cls['id']}/enrollments",
        json={"siswa_id": s_only_archived["id"]},
        headers=admin_headers,
    )

    tenant_ov = client.get(f"{BASE}/dashboard/tenant", headers=admin_headers).json()
    assert tenant_ov["unenrolled_siswa"] == 2

    platform = client.get(f"{BASE}/dashboard/platform", headers=super_headers).json()
    ov = next(t for t in platform["tenants"] if t["tenant_id"] == tenant["id"])
    assert ov["unenrolled_siswa"] == 2


def test_expanded_tenant_export_backup(client: TestClient):
    super_headers = auth_headers(client, SUPERADMIN)
    tenant, _, admin_headers = _create_tenant(client, super_headers)
    suffix = uuid.uuid4().hex[:6]

    guru = client.post(
        f"{BASE}/users",
        json={
            "email": f"g.exp.{suffix}@simulasi.local",
            "password": "Password123!",
            "full_name": "Guru Export",
            "role": "guru",
        },
        headers=admin_headers,
    ).json()
    siswa = client.post(
        f"{BASE}/users",
        json={
            "password": "Password123!",
            "full_name": "Siswa Export",
            "role": "siswa",
            "username": f"22{suffix}1",
        },
        headers=admin_headers,
    ).json()
    cls = client.post(
        f"{BASE}/classes",
        json={"name": f"Export {suffix}", "academic_year": "2026/2027", "guru_id": guru["id"]},
        headers=admin_headers,
    ).json()

    with Session(engine) as s:
        slip = WithholdingSlip(
            slip_type=SlipType.bp21,
            tenant_id=tenant["id"],
            class_id=cls["id"],
            siswa_id=siswa["id"],
            created_by_id=siswa["id"],
            status=SlipStatus.draft,
            tax_month=7,
            tax_year=2026,
            tax_object_code="21-100-01",
            tax_nature=SlipTaxNature.non_final,
            recipient_name="EXPORT SLIP",
        )
        spt = TaxReturn(
            tenant_id=tenant["id"],
            class_id=cls["id"],
            siswa_id=siswa["id"],
            created_by_id=siswa["id"],
            return_type=TaxReturnType.ppn,
            status=TaxReturnStatus.draft,
            tax_month=7,
            tax_year=2026,
        )
        billing = BillingCode(
            tenant_id=tenant["id"],
            siswa_id=siswa["id"],
            code=f"99{suffix}0000001"[:15],
            status=BillingStatus.unpaid,
            amount=50_000,
            masa_pajak="072026",
        )
        assignment = Assignment(
            tenant_id=tenant["id"],
            class_id=cls["id"],
            created_by=guru["id"],
            title=f"Tugas Export {suffix}",
            doc_type=AssignmentDocType.bp21,
            status=AssignmentStatus.published,
        )
        s.add_all([slip, spt, billing, assignment])
        s.flush()
        submission = AssignmentSubmission(
            assignment_id=assignment.id,
            siswa_id=siswa["id"],
            doc_id=slip.id,
            auto_score=100,
        )
        s.add(submission)
        s.commit()
        slip_id, spt_id, billing_id, assign_id, sub_id = (
            slip.id,
            spt.id,
            billing.id,
            assignment.id,
            submission.id,
        )

    res = client.get(f"{BASE}/tenants/{tenant['id']}/export", headers=super_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert {w["id"] for w in body["withholding_slips"]} == {slip_id}
    assert {r["id"] for r in body["tax_returns"]} == {spt_id}
    assert {b["id"] for b in body["billing_codes"]} == {billing_id}
    assert {a["id"] for a in body["assignments"]} == {assign_id}
    assert {sub["id"] for sub in body["assignment_submissions"]} == {sub_id}


def test_audit_log_search_q(client: TestClient):
    super_headers = auth_headers(client, SUPERADMIN)
    tenant, admin_email, admin_headers = _create_tenant(client, super_headers)
    suffix = uuid.uuid4().hex[:6]

    unique_name = f"UniqueAuditMarker_{suffix}"
    client.post(
        f"{BASE}/users",
        json={
            "email": f"aud.target.{suffix}@simulasi.local",
            "password": "Password123!",
            "full_name": unique_name,
            "role": "guru",
        },
        headers=admin_headers,
    )

    # Search by summary substring (case-insensitive)
    by_summary = client.get(
        f"{BASE}/audit-logs",
        params={"q": unique_name.lower()},
        headers=admin_headers,
    )
    assert by_summary.status_code == 200
    assert by_summary.json()["total"] == 1
    assert unique_name in by_summary.json()["items"][0]["summary"]

    # Search by actor_email substring (note: tenant.create log summary also mentions admin_email)
    by_actor = client.get(
        f"{BASE}/audit-logs",
        params={"q": admin_email.upper(), "tenant_id": tenant["id"]},
        headers=super_headers,
    )
    assert by_actor.status_code == 200
    items = by_actor.json()["items"]
    assert len(items) >= 1
    assert any(item["actor_email"] == admin_email for item in items)
    assert all(
        admin_email in item["actor_email"].lower() or admin_email in item["summary"].lower()
        for item in items
    )
