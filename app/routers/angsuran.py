"""Angsuran billing — ajukan (siswa), bayar per cicilan, lunas otomatis."""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlmodel import func, select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.models.angsuran import Angsuran, AngsuranStatus, Cicilan, CicilanStatus
from app.models.billing import BillingCode
from app.models.enums import BillingStatus, Role
from app.models.user import User

router = APIRouter(prefix="/angsuran", tags=["angsuran"])

_read_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru, Role.siswa))
_siswa_roles = Depends(require_roles(Role.siswa))
_pay_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru, Role.siswa))


class AngsuranCreate(BaseModel):
    billing_id: int
    n_cicilan: int = Field(ge=2, le=12, alias="n")

    model_config = {"populate_by_name": True}


class CicilanRead(BaseModel):
    id: int
    ke: int
    jatuh_tempo: date
    amount: int
    status: str
    paid_at: datetime | None
    kanal: str | None


class AngsuranRead(BaseModel):
    id: int
    billing_id: int
    n_cicilan: int
    status: str
    cicilan: list[CicilanRead]


class CicilanPay(BaseModel):
    kanal: str = Field(min_length=1, max_length=40)


class AngsuranListResponse(BaseModel):
    items: list[AngsuranRead]
    total: int


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _to_read(session, angsuran: Angsuran) -> AngsuranRead:
    cicilan = session.exec(
        select(Cicilan).where(Cicilan.angsuran_id == angsuran.id).order_by(Cicilan.ke)
    ).all()
    return AngsuranRead(
        id=angsuran.id,
        billing_id=angsuran.billing_id,
        n_cicilan=angsuran.n_cicilan,
        status=angsuran.status.value,
        cicilan=[
            CicilanRead(
                id=c.id,
                ke=c.ke,
                jatuh_tempo=c.jatuh_tempo,
                amount=c.amount,
                status=c.status.value,
                paid_at=c.paid_at,
                kanal=c.kanal,
            )
            for c in cicilan
        ],
    )


def _can_read(user: User, angsuran: Angsuran) -> bool:
    if user.role == Role.superadmin:
        return True
    if user.role == Role.siswa:
        return angsuran.user_id == user.id
    return angsuran.tenant_id == user.tenant_id


@router.post(
    "",
    response_model=AngsuranRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_siswa_roles],
)
def create_angsuran(
    data: AngsuranCreate, current_user: CurrentUser, session: SessionDep
) -> AngsuranRead:
    """Ajukan angsuran atas billing unpaid milik sendiri (langsung berjalan)."""
    billing = session.get(BillingCode, data.billing_id)
    if billing is None or billing.siswa_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Kode billing tidak ditemukan"
        )
    if billing.status == BillingStatus.paid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Billing sudah dibayar, tidak bisa diangsur",
        )
    exists = session.exec(select(Angsuran).where(Angsuran.billing_id == billing.id)).first()
    if exists is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Billing ini sudah memiliki angsuran",
        )
    angsuran = Angsuran(
        tenant_id=billing.tenant_id,
        user_id=current_user.id,
        billing_id=billing.id,
        n_cicilan=data.n_cicilan,
    )
    session.add(angsuran)
    session.flush()
    # Jadwal: total/n, sisa pembulatan di cicilan terakhir, +1 bulan tiap cicilan.
    base = billing.amount // data.n_cicilan
    today = datetime.now(tz=UTC).date()
    for ke in range(1, data.n_cicilan + 1):
        amount = billing.amount - base * (data.n_cicilan - 1) if ke == data.n_cicilan else base
        session.add(
            Cicilan(
                angsuran_id=angsuran.id,
                ke=ke,
                jatuh_tempo=_add_months(today, ke),
                amount=amount,
            )
        )
    session.commit()
    session.refresh(angsuran)
    return _to_read(session, angsuran)


@router.get("", response_model=AngsuranListResponse, dependencies=[_read_roles])
def list_angsuran(
    current_user: CurrentUser,
    session: SessionDep,
    tenant_id: Annotated[int | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AngsuranListResponse:
    """Sendiri (siswa); se-tenant untuk guru/admin via ?tenant_id."""
    conditions = []
    if current_user.role == Role.siswa:
        conditions.append(Angsuran.user_id == current_user.id)
    else:
        target = tenant_id if tenant_id is not None else current_user.tenant_id
        if current_user.role != Role.superadmin and target != current_user.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Akses lintas tenant ditolak"
            )
        if target is not None:
            conditions.append(Angsuran.tenant_id == target)
    total = session.exec(select(func.count()).select_from(Angsuran).where(*conditions)).one()
    items = session.exec(
        select(Angsuran)
        .where(*conditions)
        .order_by(Angsuran.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    return AngsuranListResponse(items=[_to_read(session, a) for a in items], total=total)


@router.get("/{angsuran_id}", response_model=AngsuranRead, dependencies=[_read_roles])
def get_angsuran(angsuran_id: int, current_user: CurrentUser, session: SessionDep) -> AngsuranRead:
    angsuran = session.get(Angsuran, angsuran_id)
    if angsuran is None or not _can_read(current_user, angsuran):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Angsuran tidak ditemukan"
        )
    return _to_read(session, angsuran)


@router.post("/{angsuran_id}/batal", response_model=AngsuranRead, dependencies=[_pay_roles])
def batal_angsuran(
    angsuran_id: int, current_user: CurrentUser, session: SessionDep
) -> AngsuranRead:
    angsuran = session.get(Angsuran, angsuran_id)
    if angsuran is None or not _can_read(current_user, angsuran):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Angsuran tidak ditemukan"
        )
    if angsuran.status != AngsuranStatus.berjalan:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Hanya angsuran berjalan yang bisa dibatalkan",
        )
    paid = session.exec(
        select(Cicilan).where(
            Cicilan.angsuran_id == angsuran.id, Cicilan.status == CicilanStatus.lunas
        )
    ).first()
    if paid is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Angsuran yang sudah ada cicilan lunas tidak bisa dibatalkan",
        )
    angsuran.status = AngsuranStatus.batal
    session.add(angsuran)
    session.commit()
    session.refresh(angsuran)
    return _to_read(session, angsuran)


@router.post("/cicilan/{cicilan_id}/pay", response_model=CicilanRead, dependencies=[_pay_roles])
def pay_cicilan(
    cicilan_id: int, data: CicilanPay, current_user: CurrentUser, session: SessionDep
) -> CicilanRead:
    """Bayar satu cicilan. Bila semua lunas → angsuran lunas + billing paid + tagihan lunas."""
    from app.routers.tagihan import lunaskan_tagihan_untuk_billing

    cicilan = session.get(Cicilan, cicilan_id)
    if cicilan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cicilan tidak ditemukan"
        )
    angsuran = session.get(Angsuran, cicilan.angsuran_id)
    if angsuran is None or not _can_read(current_user, angsuran):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cicilan tidak ditemukan"
        )
    if cicilan.status == CicilanStatus.lunas:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cicilan sudah dibayar",
        )
    if angsuran.status != AngsuranStatus.berjalan:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Angsuran tidak sedang berjalan",
        )
    cicilan.status = CicilanStatus.lunas
    cicilan.paid_at = datetime.now(tz=UTC)
    cicilan.kanal = data.kanal
    session.add(cicilan)
    session.flush()
    remaining = session.exec(
        select(func.count())
        .select_from(Cicilan)
        .where(Cicilan.angsuran_id == angsuran.id, Cicilan.status == CicilanStatus.belum)
    ).one()
    if remaining == 0:
        angsuran.status = AngsuranStatus.lunas
        session.add(angsuran)
        billing = session.get(BillingCode, angsuran.billing_id)
        if billing is not None and billing.status != BillingStatus.paid:
            billing.status = BillingStatus.paid
            billing.paid_at = datetime.now(tz=UTC)
            session.add(billing)
        lunaskan_tagihan_untuk_billing(session, angsuran.billing_id)
    session.commit()
    session.refresh(cicilan)
    return CicilanRead(
        id=cicilan.id,
        ke=cicilan.ke,
        jatuh_tempo=cicilan.jatuh_tempo,
        amount=cicilan.amount,
        status=cicilan.status.value,
        paid_at=cicilan.paid_at,
        kanal=cicilan.kanal,
    )
