"""Tenant management routes (superadmin only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select

from app.core.deps import SessionDep, require_roles
from app.models.enums import Role
from app.models.tenant import Tenant
from app.schemas.tenant import TenantCreate, TenantRead, TenantUpdate

router = APIRouter(prefix="/tenants", tags=["tenants"])

_superadmin = Depends(require_roles(Role.superadmin))


@router.get("", response_model=list[TenantRead], dependencies=[_superadmin])
def list_tenants(session: SessionDep) -> list[Tenant]:
    return session.exec(select(Tenant).order_by(Tenant.id)).all()


@router.post(
    "",
    response_model=TenantRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_superadmin],
)
def create_tenant(data: TenantCreate, session: SessionDep) -> Tenant:
    exists = session.exec(select(Tenant).where(Tenant.slug == data.slug)).first()
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Slug sudah digunakan")
    tenant = Tenant(name=data.name, slug=data.slug, type=data.type)
    session.add(tenant)
    session.commit()
    session.refresh(tenant)
    return tenant


@router.patch("/{tenant_id}", response_model=TenantRead, dependencies=[_superadmin])
def update_tenant(tenant_id: int, data: TenantUpdate, session: SessionDep) -> Tenant:
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant tidak ditemukan")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(tenant, field, value)
    session.add(tenant)
    session.commit()
    session.refresh(tenant)
    return tenant
