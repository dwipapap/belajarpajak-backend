"""Integration tests for Pengaturan Instansi (module toggles, package, learning settings)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.core.modules import ALL_MODULE_KEYS
from tests.conftest import auth_headers

BASE = "/api/v1"
SUPERADMIN = "super@pajaksim.local"
SEED_ADMIN = "admin@smkn1-pku.local"


def _setup_throwaway_tenant(client: TestClient) -> dict:
    """Create a fresh tenant with an admin, a guru, a class, and a siswa."""
    super_headers = auth_headers(client, SUPERADMIN)
    suffix = uuid.uuid4().hex[:8]
    admin_email = f"admin.peng-{suffix}@simulasi.local"
    res = client.post(
        f"{BASE}/tenants",
        json={
            "name": f"SMK Pengaturan {suffix}",
            "slug": f"smk-peng-{suffix}",
            "type": "smk",
            "masa_berlaku_bulan": 12,
            "admin": {
                "email": admin_email,
                "full_name": f"Admin Pengaturan {suffix}",
            },
        },
        headers=super_headers,
    )
    assert res.status_code == 201, res.text
    tenant = res.json()

    admin_headers = auth_headers(client, admin_email)

    guru_email = f"guru.peng-{suffix}@simulasi.local"
    res_guru = client.post(
        f"{BASE}/users",
        json={
            "email": guru_email,
            "full_name": f"Guru Pengaturan {suffix}",
            "role": "guru",
            "password": "Password123!",
        },
        headers=admin_headers,
    )
    assert res_guru.status_code == 201, res_guru.text
    guru = res_guru.json()
    guru_headers = auth_headers(client, guru_email)

    res_cls = client.post(
        f"{BASE}/classes",
        json={
            "name": f"XII Pajak {suffix}",
            "academic_year": "2025/2026",
            "guru_id": guru["id"],
        },
        headers=admin_headers,
    )
    assert res_cls.status_code == 201, res_cls.text
    school_class = res_cls.json()

    siswa_email = f"siswa.peng-{suffix}@simulasi.local"
    siswa_username = f"99{suffix}"
    res_siswa = client.post(
        f"{BASE}/users",
        json={
            "email": siswa_email,
            "username": siswa_username,
            "full_name": f"Siswa Pengaturan {suffix}",
            "role": "siswa",
            "password": "Password123!",
        },
        headers=admin_headers,
    )
    assert res_siswa.status_code == 201, res_siswa.text
    siswa = res_siswa.json()
    siswa_headers = auth_headers(client, siswa_email)

    res_enroll = client.post(
        f"{BASE}/classes/{school_class['id']}/enrollments",
        json={"siswa_id": siswa["id"]},
        headers=admin_headers,
    )
    assert res_enroll.status_code in (200, 201), res_enroll.text

    return {
        "tenant": tenant,
        "super_headers": super_headers,
        "admin_email": admin_email,
        "admin_headers": admin_headers,
        "guru_email": guru_email,
        "guru_headers": guru_headers,
        "siswa_email": siswa_email,
        "siswa_headers": siswa_headers,
        "class_id": school_class["id"],
    }


def _create_issued_bppu(
    client: TestClient, siswa_headers: dict[str, str], doc_no: str = "FP-100"
) -> dict:
    res = client.post(
        f"{BASE}/bppu",
        json={
            "tax_month": 5,
            "tax_year": 2025,
            "recipient_identity_number": "0123456789012345",
            "recipient_name": "PT Mitra Pengaturan",
            "tax_object_code": "22-100-07",
            "tax_nature": "non_final",
            "tax_facility": "none",
            "dpp": 10_000_000,
            "rate_percent": 0.25,
            "document_type": "Faktur Pajak",
            "document_number": doc_no,
            "document_date": "2025-05-15",
        },
        headers=siswa_headers,
    )
    assert res.status_code == 201, res.text
    doc = res.json()
    issue = client.post(
        f"{BASE}/bppu/{doc['id']}/issue",
        json={"kode_otorisasi": "123456", "signer_name": "Siswa Pengaturan"},
        headers=siswa_headers,
    )
    assert issue.status_code == 200, issue.text
    return issue.json()


def test_default_all_on_and_auth_me(client: TestClient):
    seed_admin_headers = auth_headers(client, SEED_ADMIN)
    res_peng = client.get(f"{BASE}/pengaturan", headers=seed_admin_headers)
    assert res_peng.status_code == 200, res_peng.text
    body = res_peng.json()
    assert set(body["allowed_modules"]) == set(ALL_MODULE_KEYS)
    assert set(body["enabled_modules"]) == set(ALL_MODULE_KEYS)
    assert body["settings"] == {
        "show_auto_check_details": True,
        "allow_late_submission": True,
        "allow_resubmission": True,
    }
    assert len(body["module_catalog"]) == len(ALL_MODULE_KEYS)

    res_me = client.get(f"{BASE}/auth/me", headers=seed_admin_headers)
    assert res_me.status_code == 200, res_me.text
    me = res_me.json()
    assert set(me["enabled_modules"]) == set(ALL_MODULE_KEYS)
    assert me["settings"]["show_auto_check_details"] is True


def test_rbac_and_tenant_scoping(client: TestClient):
    ctx = _setup_throwaway_tenant(client)
    other_ctx = _setup_throwaway_tenant(client)

    # Guru and siswa cannot read or patch pengaturan
    for hdrs in (ctx["guru_headers"], ctx["siswa_headers"]):
        assert client.get(f"{BASE}/pengaturan", headers=hdrs).status_code == 403
        assert (
            client.patch(
                f"{BASE}/pengaturan",
                json={"enabled_modules": list(ALL_MODULE_KEYS)},
                headers=hdrs,
            ).status_code
            == 403
        )

    # Admin cannot read or patch another tenant
    other_id = other_ctx["tenant"]["id"]
    assert (
        client.get(
            f"{BASE}/pengaturan?tenant_id={other_id}",
            headers=ctx["admin_headers"],
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"{BASE}/pengaturan?tenant_id={other_id}",
            json={"enabled_modules": list(ALL_MODULE_KEYS)},
            headers=ctx["admin_headers"],
        ).status_code
        == 403
    )

    # Admin cannot call superadmin package endpoint
    assert (
        client.patch(
            f"{BASE}/pengaturan/paket?tenant_id={ctx['tenant']['id']}",
            json={"allowed_modules": ["bppu"]},
            headers=ctx["admin_headers"],
        ).status_code
        == 403
    )

    # Superadmin must provide tenant_id
    assert client.get(f"{BASE}/pengaturan", headers=ctx["super_headers"]).status_code == 422


def test_package_control_and_admin_subset_validation(client: TestClient):
    ctx = _setup_throwaway_tenant(client)
    tid = ctx["tenant"]["id"]

    # Superadmin restricts package to only bppu, faktur_keluaran, tugas
    res_pkg = client.patch(
        f"{BASE}/pengaturan/paket?tenant_id={tid}",
        json={"allowed_modules": ["bppu", "faktur_keluaran", "tugas"]},
        headers=ctx["super_headers"],
    )
    assert res_pkg.status_code == 200, res_pkg.text
    pkg_body = res_pkg.json()
    assert pkg_body["allowed_modules"] == ["faktur_keluaran", "bppu", "tugas"]
    assert pkg_body["enabled_modules"] == ["faktur_keluaran", "bppu", "tugas"]

    # Admin cannot enable a module outside the package (e.g. bpnr) -> 422
    res_outside = client.patch(
        f"{BASE}/pengaturan",
        json={"enabled_modules": ["bppu", "bpnr"]},
        headers=ctx["admin_headers"],
    )
    assert res_outside.status_code == 422, res_outside.text
    assert "bpnr" in res_outside.json()["detail"]

    # Admin can toggle within the package
    res_inside = client.patch(
        f"{BASE}/pengaturan",
        json={"enabled_modules": ["bppu", "tugas"]},
        headers=ctx["admin_headers"],
    )
    assert res_inside.status_code == 200, res_inside.text
    assert res_inside.json()["enabled_modules"] == ["bppu", "tugas"]

    # /auth/me reflects effective enabled_modules
    me_siswa = client.get(f"{BASE}/auth/me", headers=ctx["siswa_headers"]).json()
    assert me_siswa["enabled_modules"] == ["bppu", "tugas"]

    # Audit logs recorded package change and module toggle
    res_audit = client.get(f"{BASE}/audit-logs", headers=ctx["admin_headers"])
    assert res_audit.status_code == 200, res_audit.text
    actions = [item["action"] for item in res_audit.json()["items"]]
    assert "pengaturan.paket" in actions
    assert "pengaturan.modul" in actions


def test_disabled_module_blocks_endpoints_and_preserves_data(client: TestClient):
    ctx = _setup_throwaway_tenant(client)

    # Siswa creates a BPPU while enabled
    created = _create_issued_bppu(client, ctx["siswa_headers"])
    assert client.get(f"{BASE}/bppu", headers=ctx["siswa_headers"]).status_code == 200

    # Admin disables bppu
    enabled_without_bppu = [k for k in ALL_MODULE_KEYS if k != "bppu"]
    res_toggle = client.patch(
        f"{BASE}/pengaturan",
        json={"enabled_modules": enabled_without_bppu},
        headers=ctx["admin_headers"],
    )
    assert res_toggle.status_code == 200, res_toggle.text

    # Siswa GET and POST /api/v1/bppu now return 403
    res_get = client.get(f"{BASE}/bppu", headers=ctx["siswa_headers"])
    assert res_get.status_code == 403
    assert "BPPU" in res_get.json()["detail"]

    res_post = client.post(
        f"{BASE}/bppu",
        json={
            "tax_month": 5,
            "tax_year": 2025,
            "recipient_identity_number": "0123456789012345",
            "recipient_name": "PT Ditolak",
            "tax_object_code": "22-100-07",
            "tax_nature": "non_final",
            "tax_facility": "none",
            "dpp": 5_000_000,
            "rate_percent": 0.25,
            "document_type": "Faktur Pajak",
            "document_number": "FP-999",
            "document_date": "2025-05-15",
        },
        headers=ctx["siswa_headers"],
    )
    assert res_post.status_code == 403

    # Superadmin is never blocked
    res_super = client.get(
        f"{BASE}/bppu?tenant_id={ctx['tenant']['id']}",
        headers=ctx["super_headers"],
    )
    assert res_super.status_code == 200

    # Re-enable bppu -> data is visible again
    client.patch(
        f"{BASE}/pengaturan",
        json={"enabled_modules": list(ALL_MODULE_KEYS)},
        headers=ctx["admin_headers"],
    )
    res_reenabled = client.get(f"{BASE}/bppu", headers=ctx["siswa_headers"])
    assert res_reenabled.status_code == 200
    ids = [item["id"] for item in res_reenabled.json()["items"]]
    assert created["id"] in ids


def test_spt_return_type_gating(client: TestClient):
    ctx = _setup_throwaway_tenant(client)

    # Create a PPN SPT and a PPh Unifikasi SPT while all enabled
    res_ppn = client.post(
        f"{BASE}/spt",
        json={"return_type": "ppn", "tax_month": 5, "tax_year": 2025},
        headers=ctx["siswa_headers"],
    )
    assert res_ppn.status_code == 201, res_ppn.text
    ppn_id = res_ppn.json()["id"]

    res_unif = client.post(
        f"{BASE}/spt",
        json={"return_type": "pph_unifikasi", "tax_month": 5, "tax_year": 2025},
        headers=ctx["siswa_headers"],
    )
    assert res_unif.status_code == 201, res_unif.text
    unif_id = res_unif.json()["id"]

    # Disable spt_ppn, keep spt_pph_unifikasi on
    enabled = [k for k in ALL_MODULE_KEYS if k != "spt_ppn"]
    client.patch(
        f"{BASE}/pengaturan",
        json={"enabled_modules": enabled},
        headers=ctx["admin_headers"],
    )

    # Creating another PPN SPT -> 403
    res_create_blocked = client.post(
        f"{BASE}/spt",
        json={"return_type": "ppn", "tax_month": 6, "tax_year": 2025},
        headers=ctx["siswa_headers"],
    )
    assert res_create_blocked.status_code == 403

    # Getting the existing PPN SPT by ID -> 403
    assert client.get(f"{BASE}/spt/{ppn_id}", headers=ctx["siswa_headers"]).status_code == 403

    # Getting the PPh Unifikasi SPT -> 200
    assert client.get(f"{BASE}/spt/{unif_id}", headers=ctx["siswa_headers"]).status_code == 200

    # Listing /spt filters out disabled return types
    res_list = client.get(f"{BASE}/spt", headers=ctx["siswa_headers"])
    assert res_list.status_code == 200
    listed_ids = [item["id"] for item in res_list.json()["items"]]
    assert ppn_id not in listed_ids
    assert unif_id in listed_ids


def test_tugas_disabled_doctype_and_learning_settings(client: TestClient):
    ctx = _setup_throwaway_tenant(client)

    # 1. Disable bpnr -> guru cannot create a tugas with doc_type=bpnr (403)
    enabled_no_bpnr = [k for k in ALL_MODULE_KEYS if k != "bpnr"]
    client.patch(
        f"{BASE}/pengaturan",
        json={"enabled_modules": enabled_no_bpnr},
        headers=ctx["admin_headers"],
    )
    res_blocked_tugas = client.post(
        f"{BASE}/tugas",
        json={
            "class_id": ctx["class_id"],
            "title": "Tugas BPNR Nonaktif",
            "instructions": "Test",
            "doc_type": "bpnr",
            "publish": True,
        },
        headers=ctx["guru_headers"],
    )
    assert res_blocked_tugas.status_code == 403

    # 2. Guru creates a published tugas for bppu (which is enabled)
    future_due = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    res_tugas = client.post(
        f"{BASE}/tugas",
        json={
            "class_id": ctx["class_id"],
            "title": "Tugas BPPU Pengaturan",
            "instructions": "Buat BPPU",
            "doc_type": "bppu",
            "due_at": future_due,
            "publish": True,
            "expected_spec": {"dpp": 10_000_000},
        },
        headers=ctx["guru_headers"],
    )
    assert res_tugas.status_code == 201, res_tugas.text
    tugas_id = res_tugas.json()["id"]

    # If bppu is subsequently disabled, siswa list hides it and submit returns 403
    enabled_no_bppu = [k for k in ALL_MODULE_KEYS if k != "bppu"]
    client.patch(
        f"{BASE}/pengaturan",
        json={"enabled_modules": enabled_no_bppu},
        headers=ctx["admin_headers"],
    )
    siswa_list = client.get(f"{BASE}/tugas", headers=ctx["siswa_headers"]).json()
    assert all(item["id"] != tugas_id for item in siswa_list)
    assert client.get(f"{BASE}/tugas/{tugas_id}", headers=ctx["siswa_headers"]).status_code == 403

    # Re-enable bppu
    client.patch(
        f"{BASE}/pengaturan",
        json={"enabled_modules": list(ALL_MODULE_KEYS)},
        headers=ctx["admin_headers"],
    )

    # 3. Learning settings: show_auto_check_details = False
    res_set = client.patch(
        f"{BASE}/pengaturan",
        json={"settings": {"show_auto_check_details": False}},
        headers=ctx["admin_headers"],
    )
    assert res_set.status_code == 200
    assert res_set.json()["settings"]["show_auto_check_details"] is False

    doc1 = _create_issued_bppu(client, ctx["siswa_headers"], doc_no="FP-201")
    res_sub1 = client.post(
        f"{BASE}/tugas/{tugas_id}/submit",
        json={"doc_id": doc1["id"]},
        headers=ctx["siswa_headers"],
    )
    assert res_sub1.status_code == 200, res_sub1.text
    sub1_body = res_sub1.json()
    assert sub1_body["auto_score"] is not None
    assert sub1_body["auto_checks"] == []
    assert sub1_body["auto_checks_hidden"] is True

    # Guru still sees full auto_checks
    guru_detail = client.get(f"{BASE}/tugas/{tugas_id}", headers=ctx["guru_headers"]).json()
    guru_sub = guru_detail["submissions"][0]
    assert len(guru_sub["auto_checks"]) > 0
    assert guru_sub["auto_checks_hidden"] is False

    # 4. Learning settings: allow_resubmission = False -> second submit rejected with 409
    client.patch(
        f"{BASE}/pengaturan",
        json={"settings": {"allow_resubmission": False}},
        headers=ctx["admin_headers"],
    )
    doc2 = _create_issued_bppu(client, ctx["siswa_headers"], doc_no="FP-202")
    res_resub = client.post(
        f"{BASE}/tugas/{tugas_id}/submit",
        json={"doc_id": doc2["id"]},
        headers=ctx["siswa_headers"],
    )
    assert res_resub.status_code == 409

    # 5. Learning settings: allow_late_submission = False -> submit after due_at rejected with 409
    past_due = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    res_late_tugas = client.post(
        f"{BASE}/tugas",
        json={
            "class_id": ctx["class_id"],
            "title": "Tugas Lewat Tenggat",
            "instructions": "Buat BPPU",
            "doc_type": "bppu",
            "due_at": past_due,
            "publish": True,
        },
        headers=ctx["guru_headers"],
    )
    assert res_late_tugas.status_code == 201, res_late_tugas.text
    late_tugas_id = res_late_tugas.json()["id"]

    client.patch(
        f"{BASE}/pengaturan",
        json={"settings": {"allow_late_submission": False, "allow_resubmission": True}},
        headers=ctx["admin_headers"],
    )
    res_late_sub = client.post(
        f"{BASE}/tugas/{late_tugas_id}/submit",
        json={"doc_id": doc2["id"]},
        headers=ctx["siswa_headers"],
    )
    assert res_late_sub.status_code == 409
    assert "Tenggat sudah lewat" in res_late_sub.json()["detail"]

    # Audit log has pengaturan.pembelajaran
    res_audit = client.get(f"{BASE}/audit-logs", headers=ctx["admin_headers"])
    actions = [item["action"] for item in res_audit.json()["items"]]
    assert "pengaturan.pembelajaran" in actions
