"""Superadmin CRUD for simulator PTKP and progressive tariff tables."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import select

from app.core.deps import SessionDep, require_roles
from app.models.enums import Role
from app.models.tarif_pajak import TarifProgresifPasal17, TierPtkp
from app.schemas.tarif_pajak import (
    TarifProgresifCreate,
    TarifProgresifRead,
    TarifProgresifUpdate,
    TierPtkpCreate,
    TierPtkpRead,
    TierPtkpUpdate,
)

router = APIRouter(prefix="/tarif-pajak", tags=["tarif-pajak"])

_read_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru, Role.siswa))
_superadmin = Depends(require_roles(Role.superadmin))

_INF = 10**30


def _validate_ptkp_unique(
    session: SessionDep, *, status_kode: str, tahun_pajak: int, exclude_id: int | None = None
) -> None:
    query = select(TierPtkp).where(
        TierPtkp.status_kode == status_kode,
        TierPtkp.tahun_pajak == tahun_pajak,
    )
    if exclude_id is not None:
        query = query.where(TierPtkp.id != exclude_id)
    if session.exec(query).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="PTKP untuk status dan tahun pajak tersebut sudah ada",
        )


def _validate_bracket(
    session: SessionDep, bracket: TarifProgresifPasal17, *, exclude_id: int | None = None
) -> None:
    if bracket.batas_atas is not None and bracket.batas_atas <= bracket.batas_bawah:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="batas_atas harus lebih besar dari batas_bawah",
        )
    if not bracket.is_active:
        return

    new_low = bracket.batas_bawah
    new_high = bracket.batas_atas or _INF
    query = select(TarifProgresifPasal17).where(
        TarifProgresifPasal17.tahun_pajak == bracket.tahun_pajak,
        TarifProgresifPasal17.is_active == True,  # noqa: E712
    )
    if exclude_id is not None:
        query = query.where(TarifProgresifPasal17.id != exclude_id)
    for existing in session.exec(query).all():
        old_low = existing.batas_bawah
        old_high = existing.batas_atas or _INF
        if new_low < old_high and old_low < new_high:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Bracket tarif progresif aktif tidak boleh tumpang tindih",
            )


@router.get("/ptkp", response_model=list[TierPtkpRead], dependencies=[_read_roles])
def list_ptkp(
    session: SessionDep,
    tahun_pajak: int | None = None,
    is_active: bool | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[TierPtkp]:
    query = select(TierPtkp).order_by(TierPtkp.tahun_pajak, TierPtkp.status_kode)
    if tahun_pajak is not None:
        query = query.where(TierPtkp.tahun_pajak == tahun_pajak)
    if is_active is not None:
        query = query.where(TierPtkp.is_active == is_active)
    return session.exec(query.offset((page - 1) * size).limit(size)).all()


@router.post(
    "/ptkp",
    response_model=TierPtkpRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_superadmin],
)
def create_ptkp(data: TierPtkpCreate, session: SessionDep) -> TierPtkp:
    _validate_ptkp_unique(
        session, status_kode=data.status_kode, tahun_pajak=data.tahun_pajak
    )
    tier = TierPtkp(**data.model_dump())
    session.add(tier)
    session.commit()
    session.refresh(tier)
    return tier


@router.patch("/ptkp/{ptkp_id}", response_model=TierPtkpRead, dependencies=[_superadmin])
def update_ptkp(ptkp_id: int, data: TierPtkpUpdate, session: SessionDep) -> TierPtkp:
    tier = session.get(TierPtkp, ptkp_id)
    if tier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tier PTKP tidak ditemukan"
        )
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(tier, field, value)
    _validate_ptkp_unique(
        session, status_kode=tier.status_kode, tahun_pajak=tier.tahun_pajak, exclude_id=ptkp_id
    )
    session.add(tier)
    session.commit()
    session.refresh(tier)
    return tier


@router.delete(
    "/ptkp/{ptkp_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[_superadmin],
)
def delete_ptkp(ptkp_id: int, session: SessionDep) -> None:
    tier = session.get(TierPtkp, ptkp_id)
    if tier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tier PTKP tidak ditemukan"
        )
    session.delete(tier)
    session.commit()


@router.get("/progresif", response_model=list[TarifProgresifRead], dependencies=[_read_roles])
def list_progresif(
    session: SessionDep,
    tahun_pajak: int | None = None,
    is_active: bool | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[TarifProgresifPasal17]:
    query = select(TarifProgresifPasal17).order_by(
        TarifProgresifPasal17.tahun_pajak,
        TarifProgresifPasal17.batas_bawah,
    )
    if tahun_pajak is not None:
        query = query.where(TarifProgresifPasal17.tahun_pajak == tahun_pajak)
    if is_active is not None:
        query = query.where(TarifProgresifPasal17.is_active == is_active)
    return session.exec(query.offset((page - 1) * size).limit(size)).all()


@router.post(
    "/progresif",
    response_model=TarifProgresifRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_superadmin],
)
def create_progresif(
    data: TarifProgresifCreate, session: SessionDep
) -> TarifProgresifPasal17:
    bracket = TarifProgresifPasal17(**data.model_dump())
    _validate_bracket(session, bracket)
    session.add(bracket)
    session.commit()
    session.refresh(bracket)
    return bracket


@router.patch(
    "/progresif/{bracket_id}",
    response_model=TarifProgresifRead,
    dependencies=[_superadmin],
)
def update_progresif(
    bracket_id: int, data: TarifProgresifUpdate, session: SessionDep
) -> TarifProgresifPasal17:
    bracket = session.get(TarifProgresifPasal17, bracket_id)
    if bracket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bracket tarif progresif tidak ditemukan"
        )
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(bracket, field, value)
    _validate_bracket(session, bracket, exclude_id=bracket_id)
    session.add(bracket)
    session.commit()
    session.refresh(bracket)
    return bracket


@router.delete(
    "/progresif/{bracket_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[_superadmin],
)
def delete_progresif(bracket_id: int, session: SessionDep) -> None:
    bracket = session.get(TarifProgresifPasal17, bracket_id)
    if bracket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bracket tarif progresif tidak ditemukan"
        )
    session.delete(bracket)
    session.commit()
