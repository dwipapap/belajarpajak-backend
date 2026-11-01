"""Pbk — pengajuan siswa, persetujuan guru/admin, murni pencatatan persetujuan."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlmodel import Session, func, select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.models.enums import Role
from app.models.pbk import PbkRequest, PbkStatus
from app.models.user import User

router = APIRouter(prefix="/pbk", tags=["pbk"])

_read_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru, Role.siswa))
_decide_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru))


class PbkCreate(BaseModel):
    dari_kap: str = Field(max_length=20)
    ke_kap: str = Field(max_length=20)
    amount: int = Field(ge=1, le=9_999_999_999)
    masa: str = Field(max_length=6, min_length=6)
    alasan: str = Field(min_length=1, max_length=500)


class PbkRead(BaseModel):
    id: int
    tenant_id: int
    user_id: int
    dari_kap: str
    ke_kap: str
    amount: int
    masa: str
    alasan: str
    status: str
    decided_by_id: int | None
    created_at: datetime | None


class PbkListResponse(BaseModel):
    items: list[PbkRead]
    total: int


def _to_read(p: PbkRequest) -> PbkRead:
    return PbkRead(
        id=p.id,
        tenant_id=p.tenant_id,
        user_id=p.user_id,
        dari_kap=p.dari_kap,
        ke_kap=p.ke_kap,
        amount=p.amount,
        masa=p.masa,
        alasan=p.alasan,
        status=p.status.value,
        decided_by_id=p.decided_by_id,
        created_at=p.created_at,
    )


def _validate_masa(masa: str) -> None:
    if len(masa) != 6 or not masa.isdigit() or not 1 <= int(masa[:2]) <= 12:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Masa harus MMYYYY (6 digit)",
        )


@router.post("", response_model=PbkRead, status_code=status.HTTP_201_CREATED)
def create_pbk(
    data: PbkCreate,
    current_user: CurrentUser,
    session: SessionDep,
) -> PbkRead:
    """Ajukan Pbk (siswa, untuk dirinya sendiri)."""
    if current_user.role != Role.siswa:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Hanya siswa yang dapat mengajukan Pbk",
        )
    _validate_masa(data.masa)
    if current_user.tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Siswa wajib berada dalam tenant",
        )
    pbk = PbkRequest(
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        dari_kap=data.dari_kap,
        ke_kap=data.ke_kap,
        amount=data.amount,
        masa=data.masa,
        alasan=data.alasan,
    )
    session.add(pbk)
    session.commit()
    session.refresh(pbk)
    return _to_read(pbk)


@router.get("", response_model=PbkListResponse, dependencies=[_read_roles])
def list_pbk(
    current_user: CurrentUser,
    session: SessionDep,
    tenant_id: Annotated[int | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PbkListResponse:
    """Sendiri (siswa); se-tenant untuk guru/admin via ?tenant_id."""
    conditions = []
    if current_user.role == Role.siswa:
        conditions.append(PbkRequest.user_id == current_user.id)
    else:
        target = tenant_id if tenant_id is not None else current_user.tenant_id
        if current_user.role != Role.superadmin and target != current_user.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Akses lintas tenant ditolak"
            )
        if target is not None:
            conditions.append(PbkRequest.tenant_id == target)
    total = session.exec(
        select(func.count()).select_from(PbkRequest).where(*conditions)
    ).one()
    items = session.exec(
        select(PbkRequest)
        .where(*conditions)
        .order_by(PbkRequest.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    return PbkListResponse(items=[_to_read(p) for p in items], total=total)


def _get_for_decision(pbk_id: int, current_user: User, session: Session) -> PbkRequest:
    pbk = session.get(PbkRequest, pbk_id)
    if pbk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pbk tidak ditemukan")
    if current_user.role != Role.superadmin and pbk.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pbk tidak ditemukan")
    if pbk.status != PbkStatus.diajukan:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Hanya Pbk berstatus diajukan yang bisa diputuskan",
        )
    return pbk


@router.post("/{pbk_id}/setujui", response_model=PbkRead, dependencies=[_decide_roles])
def setujui_pbk(pbk_id: int, current_user: CurrentUser, session: SessionDep) -> PbkRead:
    pbk = _get_for_decision(pbk_id, current_user, session)
    pbk.status = PbkStatus.disetujui
    pbk.decided_by_id = current_user.id
    session.add(pbk)
    session.commit()
    session.refresh(pbk)
    return _to_read(pbk)


@router.post("/{pbk_id}/tolak", response_model=PbkRead, dependencies=[_decide_roles])
def tolak_pbk(pbk_id: int, current_user: CurrentUser, session: SessionDep) -> PbkRead:
    pbk = _get_for_decision(pbk_id, current_user, session)
    pbk.status = PbkStatus.ditolak
    pbk.decided_by_id = current_user.id
    session.add(pbk)
    session.commit()
    session.refresh(pbk)
    return _to_read(pbk)
