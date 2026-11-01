"""Dompet deposit — saldo milik sendiri, sinkronisasi top-up, rekap per tenant.

Top-up masuk saldo lewat :func:`sinkronisasi_deposit`, yang dipanggil otomatis
saat ``GET /deposit`` dan eksplisit lewat ``POST /deposit/sinkron``:

1. Cari billing mandiri (``spt_id IS NULL``) KAP ``411618-100`` milik user yang
   sudah ``paid``.
2. Untuk setiap billing yang belum pernah termutasi (cek ``DepositMutation``
   dengan ``ref_type='billing'`` + ``ref_id``), catat mutasi ``topup`` dan
   tambah ``saldo`` sebesar nominal billing.
3. Idempoten: pemanggilan ulang tidak menggandakan saldo karena mutasi yang
   sama ditolak unique constraint ``uq_deposit_mutation_ref`` (dicek dulu
   agar tidak 500).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlmodel import Session, func, select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.models.billing import BillingCode
from app.models.deposit import DepositAccount, DepositMutation, DepositMutationKind
from app.models.enums import BillingStatus, Role
from app.models.user import User

#: KAP setoran deposit — top-up = billing mandiri berkode ini yang sudah paid.
DEPOSIT_KAP = "411618-100"

router = APIRouter(prefix="/deposit", tags=["deposit"])

_read_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru, Role.siswa))
_tenant_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru))


class MutationRead(BaseModel):
    id: int
    jenis: str
    amount: int
    ref_type: str | None
    ref_id: int | None
    note: str | None
    created_at: datetime | None


class DepositRead(BaseModel):
    saldo: int
    mutasi: list[MutationRead]


class RekapRow(BaseModel):
    user_id: int
    full_name: str
    saldo: int


def get_or_create_account(session: Session, user: User) -> DepositAccount:
    account = session.exec(
        select(DepositAccount).where(DepositAccount.user_id == user.id)
    ).first()
    if account is None:
        account = DepositAccount(user_id=user.id, tenant_id=user.tenant_id, saldo=0)
        session.add(account)
        session.flush()
    return account


def sinkronisasi_deposit(session: Session, user: User) -> int:
    """Kreditkan billing mandiri deposit yang paid tapi belum termutasi.

    Mengembalikan total nominal yang dikreditkan pada pemanggilan ini (0 bila
    tidak ada yang baru). Idempoten: billing yang sudah punya mutasi
    ``topup`` dilewati.
    """
    account = get_or_create_account(session, user)
    paid = session.exec(
        select(BillingCode).where(
            BillingCode.siswa_id == user.id,
            BillingCode.spt_id.is_(None),
            BillingCode.kap_kjs == DEPOSIT_KAP,
            BillingCode.status == BillingStatus.paid,
        )
    ).all()
    credited = 0
    for billing in paid:
        exists = session.exec(
            select(DepositMutation).where(
                DepositMutation.account_id == account.id,
                DepositMutation.ref_type == "billing",
                DepositMutation.ref_id == billing.id,
            )
        ).first()
        if exists is not None:
            continue
        session.add(
            DepositMutation(
                account_id=account.id,
                jenis=DepositMutationKind.topup,
                amount=billing.amount,
                ref_type="billing",
                ref_id=billing.id,
                note=f"Top-up dari billing {billing.code}",
            )
        )
        account.saldo += billing.amount
        credited += billing.amount
    session.add(account)
    session.flush()
    return credited


def _to_read(session: Session, account: DepositAccount) -> DepositRead:
    mutasi = session.exec(
        select(DepositMutation)
        .where(DepositMutation.account_id == account.id)
        .order_by(DepositMutation.id.desc())
    ).all()
    return DepositRead(
        saldo=account.saldo,
        mutasi=[
            MutationRead(
                id=m.id,
                jenis=m.jenis.value,
                amount=m.amount,
                ref_type=m.ref_type,
                ref_id=m.ref_id,
                note=m.note,
                created_at=m.created_at,
            )
            for m in mutasi
        ],
    )


@router.get("", response_model=DepositRead, dependencies=[_read_roles])
def get_my_deposit(current_user: CurrentUser, session: SessionDep) -> DepositRead:
    """Saldo + mutasi milik sendiri (sinkronisasi top-up berjalan dulu)."""
    sinkronisasi_deposit(session, current_user)
    session.commit()
    account = get_or_create_account(session, current_user)
    return _to_read(session, account)


@router.post("/sinkron", dependencies=[_read_roles])
def sinkron_deposit(current_user: CurrentUser, session: SessionDep) -> dict:
    """Kreditkan billing mandiri deposit yang paid tapi belum termutasi."""
    credited = sinkronisasi_deposit(session, current_user)
    session.commit()
    account = get_or_create_account(session, current_user)
    return {"dikreditkan": credited, "saldo": account.saldo}


@router.get("/tenant", response_model=list[RekapRow], dependencies=[_tenant_roles])
def rekap_tenant(
    current_user: CurrentUser,
    session: SessionDep,
    tenant_id: Annotated[int | None, Query()] = None,
) -> list[RekapRow]:
    """Rekap saldo per user dalam satu tenant (guru + admin)."""
    target = tenant_id if tenant_id is not None else current_user.tenant_id
    if (
        current_user.role != Role.superadmin
        and target != current_user.tenant_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Akses lintas tenant ditolak"
        )
    tenant_filter = (
        [] if current_user.role == Role.superadmin and target is None
        else [User.tenant_id == target]
    )
    users = session.exec(select(User).where(*tenant_filter).order_by(User.id)).all()
    accounts = {
        a.user_id: a
        for a in session.exec(
            select(DepositAccount).where(
                DepositAccount.user_id.in_([u.id for u in users] or [0])
            )
        ).all()
    }
    _ = func  # (total agregat tersedia via saldo per akun di atas)
    return [
        RekapRow(
            user_id=u.id,
            full_name=u.full_name,
            saldo=accounts[u.id].saldo if u.id in accounts else 0,
        )
        for u in users
    ]


def pakai_deposit(
    session: Session, user: User, amount: int, ref_type: str, ref_id: int
) -> DepositMutation:
    """Kurangi saldo sebesar amount; 422 bila saldo kurang (aturan lunas-sekaligus)."""
    account = get_or_create_account(session, user)
    if account.saldo < amount:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Saldo deposit tidak cukup ({account.saldo} < {amount})",
        )
    account.saldo -= amount
    session.add(account)
    mutation = DepositMutation(
        account_id=account.id,
        jenis=DepositMutationKind.pakai,
        amount=amount,
        ref_type=ref_type,
        ref_id=ref_id,
    )
    session.add(mutation)
    session.flush()
    return mutation


MetodeBayar = Literal["billing", "deposit"]
