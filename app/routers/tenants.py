"""Tenant (institution) management routes — superadmin only.

Creating an institution is one transaction that also mints its first admin
account; the admin then runs the institution (bulk-create guru/siswa, classes).
Deactivation locks every login while keeping the data; deletion requires
typing the school code and is preceded by ``GET /{id}/export`` for the backup.
"""

from __future__ import annotations

from datetime import date
from json import dumps, loads

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel import select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.core.security import hash_password
from app.models.enrollment import Enrollment
from app.models.enums import Role
from app.models.school_class import SchoolClass
from app.models.tax_input_invoice import TaxInputInvoice
from app.models.tax_invoice import TaxInvoice
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.tenant import (
    TenantCreate,
    TenantDeleteConfirm,
    TenantRead,
    TenantStatusUpdate,
    TenantUpdate,
)
from app.services.audit import log_action

router = APIRouter(prefix="/tenants", tags=["tenants"])

_superadmin = Depends(require_roles(Role.superadmin))


def _compute_validity(months: int) -> date:
    today = date.today()
    month = today.month - 1 + months
    year = today.year + month // 12
    month = month % 12 + 1
    # Clamp the day to the month length (28..31).
    day = min(today.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                          31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def _require_slug(session: SessionDep, slug: str, exclude_id: int | None = None) -> None:
    query = select(Tenant).where(Tenant.slug == slug)
    existing = session.exec(query).first()
    if existing is not None and existing.id != exclude_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Slug sudah digunakan")


@router.get("", response_model=list[TenantRead], dependencies=[_superadmin])
def list_tenants(session: SessionDep) -> list[Tenant]:
    return session.exec(select(Tenant).order_by(Tenant.id)).all()


@router.post(
    "",
    response_model=TenantRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_superadmin],
)
def create_tenant(data: TenantCreate, session: SessionDep, current_user: CurrentUser) -> Tenant:
    """Create the institution AND its first admin in one transaction."""
    _require_slug(session, data.slug)

    admin_email = data.admin.email.lower()
    email_taken = session.exec(select(User).where(User.email == admin_email)).first()
    if email_taken is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email admin sudah terdaftar di sistem",
        )

    tenant = Tenant(
        name=data.name,
        slug=data.slug,
        type=data.type,
        contact_wa=data.contact_wa,
        berlaku_sampai=_compute_validity(data.masa_berlaku_bulan),
        max_guru=data.max_guru,
        max_siswa=data.max_siswa,
        max_kelas=data.max_kelas,
    )
    session.add(tenant)
    session.flush()

    admin = User(
        tenant_id=tenant.id,
        email=admin_email,
        password_hash=hash_password(data.admin.password),
        must_change_password=True,
        full_name=data.admin.full_name,
        role=Role.admin,
    )
    session.add(admin)
    session.flush()

    # The actor is the authenticated superadmin — log the action.
    log_action(
        session,
        actor=current_user,
        action="tenant.create",
        entity="tenant",
        entity_id=tenant.id,
        tenant_id=tenant.id,
        summary=f"Membuat instansi {tenant.name} + admin {admin_email}",
    )
    session.commit()
    session.refresh(tenant)
    return tenant


@router.patch("/{tenant_id}", response_model=TenantRead, dependencies=[_superadmin])
def update_tenant(tenant_id: int, data: TenantUpdate, session: SessionDep) -> Tenant:
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant tidak ditemukan")

    payload = data.model_dump(exclude_unset=True)
    months = payload.pop("masa_berlaku_bulan", None)
    if months is not None:
        tenant.berlaku_sampai = _compute_validity(months)
    for field, value in payload.items():
        setattr(tenant, field, value)
    session.add(tenant)
    session.commit()
    session.refresh(tenant)
    return tenant


@router.post("/{tenant_id}/deactivate", response_model=TenantRead, dependencies=[_superadmin])
def deactivate_tenant(
    tenant_id: int, data: TenantStatusUpdate, session: SessionDep, current_user: CurrentUser
) -> Tenant:
    """Lock every login of the institution; the data stays intact."""
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant tidak ditemukan")
    if not tenant.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Instansi sudah tidak aktif"
        )

    tenant.is_active = False
    tenant.deactivated_reason = data.reason
    session.add(tenant)
    log_action(
        session,
        actor=current_user,
        action="tenant.deactivate",
        entity="tenant",
        entity_id=tenant.id,
        tenant_id=tenant.id,
        summary=data.reason,
    )
    session.commit()
    session.refresh(tenant)
    return tenant


@router.post("/{tenant_id}/activate", response_model=TenantRead, dependencies=[_superadmin])
def activate_tenant(
    tenant_id: int, data: TenantStatusUpdate, session: SessionDep, current_user: CurrentUser
) -> Tenant:
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant tidak ditemukan")
    if tenant.is_active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Instansi sudah aktif")

    tenant.is_active = True
    tenant.deactivated_reason = None
    session.add(tenant)
    log_action(
        session,
        actor=current_user,
        action="tenant.activate",
        entity="tenant",
        entity_id=tenant.id,
        tenant_id=tenant.id,
        summary=data.reason,
    )
    session.commit()
    session.refresh(tenant)
    return tenant


@router.get("/{tenant_id}/export", dependencies=[_superadmin])
def export_tenant(tenant_id: int, session: SessionDep) -> Response:
    """Full JSON backup of the institution's data, taken before deletion."""
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant tidak ditemukan")

    backup = {
        "tenant": loads(tenant.model_dump_json()),
        "users": [loads(u.model_dump_json()) for u in session.exec(
            select(User).where(User.tenant_id == tenant_id)).all()],
        "classes": [loads(c.model_dump_json()) for c in session.exec(
            select(SchoolClass).where(SchoolClass.tenant_id == tenant_id)).all()],
        "enrollments": [loads(e.model_dump_json()) for e in session.exec(
            select(Enrollment)
            .join(SchoolClass, SchoolClass.id == Enrollment.class_id)
            .where(SchoolClass.tenant_id == tenant_id)).all()],
        "tax_invoices": [loads(i.model_dump_json()) for i in session.exec(
            select(TaxInvoice).where(TaxInvoice.tenant_id == tenant_id)).all()],
        "tax_input_invoices": [loads(i.model_dump_json()) for i in session.exec(
            select(TaxInputInvoice).where(TaxInputInvoice.tenant_id == tenant_id)).all()],
    }
    content = dumps(backup, ensure_ascii=False, indent=2, default=str)
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="backup_{tenant.slug}.json"'
        },
    )


@router.delete("/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[_superadmin])
def delete_tenant(
    tenant_id: int, data: TenantDeleteConfirm, session: SessionDep, current_user: CurrentUser
) -> None:
    """Permanent deletion — requires the typed school code as confirmation."""
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant tidak ditemukan")
    if data.confirm_code.strip() != tenant.slug:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Kode konfirmasi tidak cocok dengan kode instansi",
        )

    log_action(
        session,
        actor=current_user,
        action="tenant.delete",
        entity="tenant",
        entity_id=None,
        tenant_id=tenant.id,
        summary=f"Menghapus instansi {tenant.name} ({tenant.slug}) beserta seluruh datanya",
    )
    session.delete(tenant)
    session.commit()
