"""Tagihan SKP/STP — terbit (guru/admin) → buat-billing → bayar → lunaskan.

Alur pelunasan (frontend): setelah ``POST /billings/{id}/pay`` sukses, panggil
``POST /tagihan/{id}/lunaskan-dari-billing`` dengan ``billing_id`` yang sama.
Endpoint ini memverifikasi billing tersebut ``paid`` DAN terikat ke tagihan ini
(``tagihan.billing_id``), lalu menandai tagihan ``lunas``. Verifikasi dilakukan
di sini — bukan di router billing — karena router billing milik jalur lain dan
tidak boleh diubah.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlmodel import func, select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.models.billing import BillingCode
from app.models.enums import BillingStatus, Role
from app.models.tagihan import Tagihan, TagihanJenis, TagihanStatus
from app.models.user import User

router = APIRouter(prefix="/tagihan", tags=["tagihan"])

_read_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru, Role.siswa))
_write_roles = Depends(require_roles(Role.admin, Role.guru))
_pay_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru, Role.siswa))


class TagihanCreate(BaseModel):
    siswa_id: int
    jenis: TagihanJenis
    kap_kjs: str = Field(max_length=20)
    masa: str = Field(max_length=6, min_length=6)
    amount: int = Field(ge=1, le=9_999_999_999)


class TagihanRead(BaseModel):
    id: int
    tenant_id: int
    user_id: int
    jenis: str
    kap_kjs: str
    masa: str
    amount: int
    status: str
    billing_id: int | None


class TagihanListResponse(BaseModel):
    items: list[TagihanRead]
    total: int


class LunaskanRequest(BaseModel):
    billing_id: int


def _to_read(t: Tagihan) -> TagihanRead:
    return TagihanRead(
        id=t.id,
        tenant_id=t.tenant_id,
        user_id=t.user_id,
        jenis=t.jenis.value,
        kap_kjs=t.kap_kjs,
        masa=t.masa,
        amount=t.amount,
        status=t.status.value,
        billing_id=t.billing_id,
    )


def _validate_masa(masa: str) -> None:
    if len(masa) != 6 or not masa.isdigit() or not 1 <= int(masa[:2]) <= 12:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Masa harus MMYYYY (6 digit)",
        )


def _get_accessible(tagihan_id: int, current_user: User, session) -> Tagihan:
    tagihan = session.get(Tagihan, tagihan_id)
    if tagihan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tagihan tidak ditemukan"
        )
    if current_user.role == Role.superadmin:
        return tagihan
    if current_user.role == Role.siswa and tagihan.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tagihan tidak ditemukan"
        )
    if current_user.role in (Role.admin, Role.guru) and (
        tagihan.tenant_id != current_user.tenant_id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tagihan tidak ditemukan"
        )
    return tagihan


@router.post("", response_model=TagihanRead, status_code=status.HTTP_201_CREATED, dependencies=[_write_roles])
def create_tagihan(
    data: TagihanCreate, current_user: CurrentUser, session: SessionDep
) -> TagihanRead:
    """Terbitkan tagihan SKP/STP untuk siswa satu tenant (guru + admin)."""
    _validate_masa(data.masa)
    siswa = session.get(User, data.siswa_id)
    if siswa is None or siswa.role != Role.siswa or siswa.tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Siswa tidak ditemukan di institusi Anda",
        )
    tagihan = Tagihan(
        tenant_id=siswa.tenant_id,
        user_id=siswa.id,
        jenis=data.jenis,
        kap_kjs=data.kap_kjs,
        masa=data.masa,
        amount=data.amount,
    )
    session.add(tagihan)
    session.commit()
    session.refresh(tagihan)
    return _to_read(tagihan)


@router.get("", response_model=TagihanListResponse, dependencies=[_read_roles])
def list_tagihan(
    current_user: CurrentUser,
    session: SessionDep,
    tenant_id: Annotated[int | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TagihanListResponse:
    """Sendiri (siswa); se-tenant untuk guru/admin via ?tenant_id."""
    conditions = []
    if current_user.role == Role.siswa:
        conditions.append(Tagihan.user_id == current_user.id)
    else:
        target = tenant_id if tenant_id is not None else current_user.tenant_id
        if current_user.role != Role.superadmin and target != current_user.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Akses lintas tenant ditolak"
            )
        if target is not None:
            conditions.append(Tagihan.tenant_id == target)
    total = session.exec(select(func.count()).select_from(Tagihan).where(*conditions)).one()
    items = session.exec(
        select(Tagihan)
        .where(*conditions)
        .order_by(Tagihan.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    return TagihanListResponse(items=[_to_read(t) for t in items], total=total)


@router.post("/{tagihan_id}/buat-billing", response_model=TagihanRead, dependencies=[_pay_roles])
def buat_billing(
    tagihan_id: int, current_user: CurrentUser, session: SessionDep
) -> TagihanRead:
    """Terbitkan satu BillingCode unpaid (1 baris = nominal tagihan)."""
    from app.services import kjs as kjs_service
    from app.services import spt as spt_service

    tagihan = _get_accessible(tagihan_id, current_user, session)
    if tagihan.status != TagihanStatus.belum:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Tagihan sudah memiliki billing",
        )
    try:
        tax_month, tax_year = kjs_service.parse_masa_pajak(tagihan.masa)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    billing = BillingCode(
        tenant_id=tagihan.tenant_id,
        siswa_id=tagihan.user_id,
        spt_id=None,
        code=spt_service.generate_code(
            session,
            tenant_id=tagihan.tenant_id,
            tax_month=tax_month,
            tax_year=tax_year,
            kap_kjs=tagihan.kap_kjs,
        ),
        amount=tagihan.amount,
        kap_kjs=tagihan.kap_kjs,
        masa_pajak=tagihan.masa,
        uraian=f"Tagihan {tagihan.jenis.value.upper()} {tagihan.masa}",
    )
    session.add(billing)
    session.flush()
    tagihan.billing_id = billing.id
    tagihan.status = TagihanStatus.dibilling
    session.add(tagihan)
    session.commit()
    session.refresh(tagihan)
    return _to_read(tagihan)


@router.post("/{tagihan_id}/lunaskan-dari-billing", response_model=TagihanRead, dependencies=[_pay_roles])
def lunaskan_dari_billing(
    tagihan_id: int, data: LunaskanRequest, current_user: CurrentUser, session: SessionDep
) -> TagihanRead:
    """Tandai lunas setelah billing terikatnya terbukti paid. Panggil setelah pay."""
    tagihan = _get_accessible(tagihan_id, current_user, session)
    if tagihan.status != TagihanStatus.dibilling:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Tagihan belum dibilling atau sudah lunas",
        )
    billing = session.get(BillingCode, data.billing_id)
    if (
        billing is None
        or billing.id != tagihan.billing_id
        or billing.status != BillingStatus.paid
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Billing tidak cocok atau belum dibayar",
        )
    tagihan.status = TagihanStatus.lunas
    session.add(tagihan)
    session.commit()
    session.refresh(tagihan)
    return _to_read(tagihan)


def lunaskan_tagihan_untuk_billing(session, billing_id: int) -> None:
    """Samakan status tagihan ber-billing ini menjadi lunas (dipakai C2)."""
    tagihan = session.exec(select(Tagihan).where(Tagihan.billing_id == billing_id)).first()
    if tagihan is not None and tagihan.status == TagihanStatus.dibilling:
        tagihan.status = TagihanStatus.lunas
        session.add(tagihan)
        session.flush()


@router.get("/{tagihan_id}", response_model=TagihanRead, dependencies=[_read_roles])
def get_tagihan(tagihan_id: int, current_user: CurrentUser, session: SessionDep) -> TagihanRead:
    return _to_read(_get_accessible(tagihan_id, current_user, session))
