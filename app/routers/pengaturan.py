"""Pengaturan Instansi routes — per-school module toggles and learning settings."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import func, select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.core.modules import (
    ALL_MODULE_KEYS,
    ALL_MODULE_SET,
    MODULE_CATALOG,
    resolve_allowed_modules,
    resolve_effective_modules,
    resolve_enabled_modules,
    resolve_tenant_settings,
)
from app.models.enums import Role
from app.models.school_class import SchoolClass
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.auth import LearningSettingsRead
from app.schemas.pengaturan import (
    ModuleCatalogItem,
    PaketModulUpdate,
    PengaturanProfile,
    PengaturanQuotas,
    PengaturanRead,
    PengaturanUpdate,
)
from app.schemas.tenant import TenantQuotaUsage
from app.services.audit import log_action

router = APIRouter(prefix="/pengaturan", tags=["pengaturan"])

_admin_or_superadmin = Depends(require_roles(Role.admin, Role.superadmin))
_superadmin_only = Depends(require_roles(Role.superadmin))


def _resolve_target_tenant(
    session: SessionDep,
    current_user: User,
    tenant_id: int | None,
) -> Tenant:
    if current_user.role == Role.superadmin:
        if tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Parameter tenant_id wajib diisi oleh superadmin",
            )
        target_id = tenant_id
    else:
        if tenant_id is not None and tenant_id != current_user.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Anda hanya dapat mengakses pengaturan instansi Anda sendiri",
            )
        target_id = current_user.tenant_id

    if target_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Instansi tidak ditemukan"
        )
    tenant = session.get(Tenant, target_id)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Instansi tidak ditemukan"
        )
    return tenant


def _build_pengaturan_read(session: SessionDep, tenant: Tenant) -> PengaturanRead:
    def _count_users(role: Role) -> int:
        return session.exec(
            select(func.count())
            .select_from(User)
            .where(User.tenant_id == tenant.id, User.role == role)
        ).one()

    guru_used = _count_users(Role.guru)
    siswa_used = _count_users(Role.siswa)
    kelas_used = session.exec(
        select(func.count())
        .select_from(SchoolClass)
        .where(SchoolClass.tenant_id == tenant.id)
    ).one()

    def _usage(used: int, limit: int | None) -> TenantQuotaUsage:
        lim = limit or None
        return TenantQuotaUsage(
            used=used, limit=lim, full=lim is not None and used >= lim
        )

    days_remaining: int | None = None
    expired = False
    expired_soon = False
    if tenant.berlaku_sampai is not None:
        days_remaining = (tenant.berlaku_sampai - date.today()).days
        expired = tenant.berlaku_sampai < date.today()
        expired_soon = days_remaining <= 30

    profile = PengaturanProfile(
        tenant_id=tenant.id,
        name=tenant.name,
        slug=tenant.slug,
        type=tenant.type,
        is_active=tenant.is_active,
        contact_wa=tenant.contact_wa,
        berlaku_dari=tenant.berlaku_dari,
        berlaku_sampai=tenant.berlaku_sampai,
        days_remaining=days_remaining,
        expired=expired,
        expired_soon=expired_soon,
        quotas=PengaturanQuotas(
            guru=_usage(guru_used, tenant.max_guru),
            siswa=_usage(siswa_used, tenant.max_siswa),
            kelas=_usage(kelas_used, tenant.max_kelas),
        ),
    )

    catalog = [
        ModuleCatalogItem(
            key=m.key,
            label=m.label,
            group=m.group,
            depends_on=list(m.depends_on),
            dependency_hint=m.dependency_hint,
        )
        for m in MODULE_CATALOG
    ]

    allowed = resolve_allowed_modules(tenant)
    effective = resolve_effective_modules(tenant)
    # enabled_modules reported to the UI is the effective subset within allowed
    # so school admin switches reflect allowed ∩ enabled cleanly.
    enabled = [k for k in resolve_enabled_modules(tenant) if k in set(allowed)]
    settings_dict = resolve_tenant_settings(tenant)

    return PengaturanRead(
        profile=profile,
        module_catalog=catalog,
        allowed_modules=allowed,
        enabled_modules=enabled,
        effective=effective,
        settings=LearningSettingsRead(**settings_dict),
    )


@router.get("", response_model=PengaturanRead, dependencies=[_admin_or_superadmin])
def get_pengaturan(
    current_user: CurrentUser,
    session: SessionDep,
    tenant_id: int | None = None,
) -> PengaturanRead:
    tenant = _resolve_target_tenant(session, current_user, tenant_id)
    return _build_pengaturan_read(session, tenant)


@router.patch("", response_model=PengaturanRead, dependencies=[_admin_or_superadmin])
def update_pengaturan(
    data: PengaturanUpdate,
    current_user: CurrentUser,
    session: SessionDep,
    tenant_id: int | None = None,
) -> PengaturanRead:
    tenant = _resolve_target_tenant(session, current_user, tenant_id)
    allowed_set = set(resolve_allowed_modules(tenant))

    if data.enabled_modules is not None:
        invalid_unknown = [m for m in data.enabled_modules if m not in ALL_MODULE_SET]
        if invalid_unknown:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Kode modul tidak dikenal: {', '.join(invalid_unknown)}",
            )
        outside_package = [m for m in data.enabled_modules if m not in allowed_set]
        if outside_package:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Modul berikut tidak termasuk dalam paket instansi Anda: "
                    f"{', '.join(outside_package)}"
                ),
            )
        req_set = set(data.enabled_modules)
        ordered = [k for k in ALL_MODULE_KEYS if k in req_set]
        tenant.enabled_modules = ordered
        log_action(
            session,
            actor=current_user,
            action="pengaturan.modul",
            entity="tenant",
            entity_id=tenant.id,
            tenant_id=tenant.id,
            summary=(
                f"Mengubah modul aktif instansi {tenant.name}: "
                f"{', '.join(ordered) if ordered else 'tidak ada modul aktif'}"
            ),
        )

    if data.settings is not None:
        current_settings = resolve_tenant_settings(tenant)
        patch = data.settings.model_dump(exclude_unset=True)
        changed_parts: list[str] = []
        for k, v in patch.items():
            if v is not None:
                current_settings[k] = bool(v)
                changed_parts.append(f"{k}={bool(v)}")
        tenant.settings = current_settings
        if changed_parts:
            log_action(
                session,
                actor=current_user,
                action="pengaturan.pembelajaran",
                entity="tenant",
                entity_id=tenant.id,
                tenant_id=tenant.id,
                summary=(
                    f"Mengubah pengaturan pembelajaran instansi {tenant.name}: "
                    f"{', '.join(changed_parts)}"
                ),
            )

    session.add(tenant)
    session.commit()
    session.refresh(tenant)
    return _build_pengaturan_read(session, tenant)


@router.patch(
    "/paket",
    response_model=PengaturanRead,
    dependencies=[_superadmin_only],
)
def update_paket_modul(
    data: PaketModulUpdate,
    current_user: CurrentUser,
    session: SessionDep,
    tenant_id: int | None = None,
) -> PengaturanRead:
    tenant = _resolve_target_tenant(session, current_user, tenant_id)
    if data.allowed_modules is not None:
        invalid = [m for m in data.allowed_modules if m not in ALL_MODULE_SET]
        if invalid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Kode modul tidak dikenal: {', '.join(invalid)}",
            )
        req_set = set(data.allowed_modules)
        ordered = [k for k in ALL_MODULE_KEYS if k in req_set]
        tenant.allowed_modules = (
            None if len(ordered) == len(ALL_MODULE_KEYS) else ordered
        )
        summary_list = (
            "semua modul"
            if tenant.allowed_modules is None
            else (", ".join(ordered) if ordered else "tidak ada modul")
        )
    else:
        tenant.allowed_modules = None
        summary_list = "semua modul"

    log_action(
        session,
        actor=current_user,
        action="pengaturan.paket",
        entity="tenant",
        entity_id=tenant.id,
        tenant_id=tenant.id,
        summary=f"Mengubah paket modul instansi {tenant.name}: {summary_list}",
    )
    session.add(tenant)
    session.commit()
    session.refresh(tenant)
    return _build_pengaturan_read(session, tenant)
