"""Simulated SPT Masa routes — Konsep → Pernyataan → Bayar dan Lapor.

The lifecycle mirrors the real portal sidebar: a draft is "Konsep SPT",
``awaiting_payment`` is "SPT menunggu pembayaran", ``reported`` is "SPT yang
dilaporkan". Posting recomputes every figure from source documents (faktur and
bupot); submit decides kurang bayar (billing issued, SPT parked awaiting
payment) versus lebih bayar/nihil (reported immediately).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlmodel import Session, func, select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.core.security import verify_password
from app.models.billing import BillingCode
from app.models.enums import (
    Role,
    TaxReturnKind,
    TaxReturnStatus,
    TaxReturnType,
)
from app.models.school_class import SchoolClass
from app.models.tax_return import TaxReturn, TaxReturnLine
from app.models.user import User
from app.schemas.tax_return import (
    BillingRead,
    TaxReturnCreate,
    TaxReturnListResponse,
    TaxReturnPernyataan,
    TaxReturnRead,
    TaxReturnReview,
    TaxReturnSubmit,
    TaxReturnSummary,
)
from app.services import spt as spt_service

LABEL = "SPT Masa"

router = APIRouter(prefix="/spt", tags=["spt"])

_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru, Role.siswa))
_review_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru))


# --- access helpers -----------------------------------------------------------


def can_read_return(session: Session, current_user: User, tax_return: TaxReturn) -> bool:
    if current_user.role == Role.superadmin:
        return True
    if current_user.role == Role.admin:
        return tax_return.tenant_id == current_user.tenant_id
    if current_user.role == Role.siswa:
        return tax_return.siswa_id == current_user.id
    if current_user.role == Role.guru and tax_return.class_id is not None:
        school_class = session.get(SchoolClass, tax_return.class_id)
        return school_class is not None and school_class.guru_id == current_user.id
    return False


def apply_access_filters(query, current_user: User):
    if current_user.role == Role.superadmin:
        return query
    if current_user.role == Role.admin:
        return query.where(TaxReturn.tenant_id == current_user.tenant_id)
    if current_user.role == Role.siswa:
        return query.where(TaxReturn.siswa_id == current_user.id)

    taught_class_ids = select(SchoolClass.id).where(SchoolClass.guru_id == current_user.id)
    return query.where(TaxReturn.class_id.in_(taught_class_ids))


def _get_accessible_return(
    return_id: int, current_user: CurrentUser, session: SessionDep
) -> TaxReturn:
    tax_return = session.get(TaxReturn, return_id)
    if tax_return is None or not can_read_return(session, current_user, tax_return):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"{LABEL} tidak ditemukan"
        )
    return tax_return


def return_to_read(session: Session, tax_return: TaxReturn) -> TaxReturnRead:
    lines = session.exec(
        select(TaxReturnLine)
        .where(TaxReturnLine.return_id == tax_return.id)
        .order_by(TaxReturnLine.id)
    ).all()
    return TaxReturnRead.model_validate(
        {
            **tax_return.model_dump(),
            "lines": [line.model_dump() for line in lines],
        }
    )


def _reject_future_period(tax_year: int, tax_month: int) -> None:
    today = datetime.now(tz=UTC)
    if (tax_year, tax_month) > (today.year, today.month):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Periode SPT tidak boleh masa depan",
        )


# --- routes -------------------------------------------------------------------


@router.get("", response_model=TaxReturnListResponse, dependencies=[_roles])
def list_returns(
    current_user: CurrentUser,
    session: SessionDep,
    return_type: TaxReturnType | None = None,
    status_filter: Annotated[TaxReturnStatus | None, Query(alias="status")] = None,
    tenant_id: int | None = None,
    class_id: int | None = None,
    siswa_id: int | None = None,
    tax_year: Annotated[int | None, Query(ge=2020, le=2100)] = None,
    tax_month: Annotated[int | None, Query(ge=1, le=12)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TaxReturnListResponse:
    conditions = []
    if return_type is not None:
        conditions.append(TaxReturn.return_type == return_type)
    if status_filter is not None:
        conditions.append(TaxReturn.status == status_filter)
    if current_user.role == Role.superadmin and tenant_id is not None:
        conditions.append(TaxReturn.tenant_id == tenant_id)
    if class_id is not None:
        conditions.append(TaxReturn.class_id == class_id)
    if siswa_id is not None and current_user.role != Role.siswa:
        conditions.append(TaxReturn.siswa_id == siswa_id)
    if tax_year is not None:
        conditions.append(TaxReturn.tax_year == tax_year)
    if tax_month is not None:
        conditions.append(TaxReturn.tax_month == tax_month)

    base_count = apply_access_filters(
        select(func.count()).select_from(TaxReturn).where(*conditions),
        current_user,
    )
    base_items = apply_access_filters(
        select(TaxReturn)
        .where(*conditions)
        .order_by(TaxReturn.id.desc())
        .offset((page - 1) * size)
        .limit(size),
        current_user,
    )
    total = session.exec(base_count).one()
    items = session.exec(base_items).all()
    return TaxReturnListResponse(
        items=[return_to_read(session, item) for item in items],
        total=total,
    )


@router.get("/summary", response_model=TaxReturnSummary, dependencies=[_roles])
def return_summary(
    current_user: CurrentUser,
    session: SessionDep,
    return_type: TaxReturnType | None = None,
) -> TaxReturnSummary:
    query = apply_access_filters(
        select(TaxReturn.status, func.count())
        .select_from(TaxReturn)
        .where(*([] if return_type is None else [TaxReturn.return_type == return_type]))
        .group_by(TaxReturn.status),
        current_user,
    )
    counts = dict(session.exec(query).all())
    return TaxReturnSummary(
        draft=counts.get(TaxReturnStatus.draft, 0),
        awaiting_payment=counts.get(TaxReturnStatus.awaiting_payment, 0),
        reported=counts.get(TaxReturnStatus.reported, 0),
        total=sum(counts.values()),
    )


@router.post(
    "", response_model=TaxReturnRead, status_code=status.HTTP_201_CREATED, dependencies=[_roles]
)
def create_return(
    data: TaxReturnCreate,
    current_user: CurrentUser,
    session: SessionDep,
) -> TaxReturnRead:
    _reject_future_period(data.tax_year, data.tax_month)

    siswa_id = (
        current_user.id if current_user.role == Role.siswa else (data.siswa_id or current_user.id)
    )

    existing = session.exec(
        select(TaxReturn).where(
            TaxReturn.tenant_id == current_user.tenant_id,
            TaxReturn.siswa_id == siswa_id,
            TaxReturn.return_type == data.return_type,
            TaxReturn.tax_year == data.tax_year,
            TaxReturn.tax_month == data.tax_month,
        )
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"SPT {data.return_type.value} periode "
                f"{data.tax_month:02d}/{data.tax_year} sudah ada"
            ),
        )
    if data.kind == TaxReturnKind.pembetulan:
        filed = session.exec(
            select(TaxReturn).where(
                TaxReturn.tenant_id == current_user.tenant_id,
                TaxReturn.siswa_id == siswa_id,
                TaxReturn.return_type == data.return_type,
                TaxReturn.tax_year == data.tax_year,
                TaxReturn.tax_month == data.tax_month,
                TaxReturn.status == TaxReturnStatus.reported,
            )
        ).first()
        # Unreachable via the uniqueness check above, but kept as a guard if the
        # same-period SPT exists yet was never reported.
        if filed is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Pembetulan hanya bisa dibuat untuk SPT yang sudah dilaporkan",
            )

    tax_return = TaxReturn(
        tenant_id=current_user.tenant_id,
        class_id=data.class_id,
        siswa_id=siswa_id,
        created_by_id=current_user.id,
        return_type=data.return_type,
        kind=data.kind,
        tax_month=data.tax_month,
        tax_year=data.tax_year,
    )
    session.add(tax_return)
    session.commit()
    session.refresh(tax_return)
    return return_to_read(session, tax_return)


@router.get("/{return_id}", response_model=TaxReturnRead, dependencies=[_roles])
def get_return(
    return_id: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> TaxReturnRead:
    return return_to_read(session, _get_accessible_return(return_id, current_user, session))


@router.post("/{return_id}/posting", response_model=TaxReturnRead, dependencies=[_roles])
def post_return(
    return_id: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> TaxReturnRead:
    tax_return = _get_accessible_return(return_id, current_user, session)
    if tax_return.status != TaxReturnStatus.draft:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Hanya SPT berstatus konsep yang bisa diposting",
        )
    spt_service.posting(session, tax_return)
    session.commit()
    session.refresh(tax_return)
    return return_to_read(session, tax_return)


@router.post("/{return_id}/pernyataan", response_model=TaxReturnRead, dependencies=[_roles])
def save_pernyataan(
    return_id: int,
    data: TaxReturnPernyataan,
    current_user: CurrentUser,
    session: SessionDep,
) -> TaxReturnRead:
    tax_return = _get_accessible_return(return_id, current_user, session)
    if tax_return.status != TaxReturnStatus.draft:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Pernyataan hanya bisa diisi saat SPT masih konsep",
        )
    tax_return.signer_name = data.signer_name
    tax_return.signer_npwp = data.signer_npwp
    tax_return.signer_capacity = data.signer_capacity
    session.add(tax_return)
    session.commit()
    session.refresh(tax_return)
    return return_to_read(session, tax_return)


@router.post("/{return_id}/submit", response_model=TaxReturnRead, dependencies=[_roles])
def submit_return(
    return_id: int,
    data: TaxReturnSubmit,
    current_user: CurrentUser,
    session: SessionDep,
) -> TaxReturnRead:
    """Bayar dan Lapor.

    PPN dengan nihil/lebih bayar (dan PPh tanpa nominal) langsung dilaporkan;
    kurang bayar membuat kode billing dan memarkir SPT di 'menunggu pembayaran'.
    """
    tax_return = _get_accessible_return(return_id, current_user, session)
    if tax_return.status != TaxReturnStatus.draft:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Hanya SPT berstatus konsep yang bisa dilaporkan",
        )
    if not tax_return.signer_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Isi pernyataan (penanda tangan) sebelum bayar dan lapor",
        )
    if not verify_password(data.passphrase, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Passphrase tidak valid",
        )

    # Numbers must reflect the documents as of this moment.
    spt_service.posting(session, tax_return)

    if tax_return.amount_due > 0:
        spt_service.ensure_billing(session, tax_return)
        tax_return.status = TaxReturnStatus.awaiting_payment
    else:
        spt_service.mark_sources_reported(session, tax_return)
        tax_return.status = TaxReturnStatus.reported
        tax_return.submitted_at = datetime.now(tz=UTC)
        tax_return.submitted_by_id = current_user.id
    session.add(tax_return)
    session.commit()
    session.refresh(tax_return)
    return return_to_read(session, tax_return)


@router.post("/{return_id}/pembetulan", response_model=TaxReturnRead, dependencies=[_roles])
def create_pembetulan(
    return_id: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> TaxReturnRead:
    tax_return = _get_accessible_return(return_id, current_user, session)
    if tax_return.status != TaxReturnStatus.reported:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Pembetulan hanya bisa dibuat untuk SPT yang sudah dilaporkan",
        )
    tax_return.kind = TaxReturnKind.pembetulan
    tax_return.correction_no += 1
    tax_return.status = TaxReturnStatus.draft
    tax_return.submitted_at = None
    tax_return.submitted_by_id = None
    for line in session.exec(
        select(TaxReturnLine).where(TaxReturnLine.return_id == tax_return.id)
    ).all():
        session.delete(line)
    tax_return.penyerahan_dpp = 0
    tax_return.penyerahan_dpp_nilai_lain = 0
    tax_return.penyerahan_ppn = 0
    tax_return.penyerahan_ppnbm = 0
    tax_return.masukan_dpp = 0
    tax_return.masukan_dpp_nilai_lain = 0
    tax_return.masukan_ppn = 0
    tax_return.kompensasi_lebih_bayar = 0
    tax_return.ppn_kurang_bayar = 0
    tax_return.ppn_lebih_bayar = 0
    tax_return.total_income_tax = 0
    tax_return.amount_due = 0
    session.add(tax_return)
    session.commit()
    session.refresh(tax_return)
    return return_to_read(session, tax_return)


@router.delete("/{return_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[_roles])
def delete_return(
    return_id: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> Response:
    """Hapus konsep SPT — only a draft may be removed, as in the real portal."""
    tax_return = _get_accessible_return(return_id, current_user, session)
    if tax_return.status != TaxReturnStatus.draft:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Hanya SPT berstatus konsep yang bisa dihapus",
        )
    session.delete(tax_return)
    session.commit()


@router.get("/{return_id}/billing", response_model=BillingRead, dependencies=[_roles])
def get_return_billing(
    return_id: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> BillingRead:
    tax_return = _get_accessible_return(return_id, current_user, session)
    billing = session.exec(
        select(BillingCode)
        .where(BillingCode.spt_id == tax_return.id)
        .order_by(BillingCode.id.desc())
    ).first()
    if billing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Belum ada kode billing untuk SPT ini",
        )
    return BillingRead.model_validate(billing.model_dump())


@router.patch("/{return_id}/review", response_model=TaxReturnRead, dependencies=[_review_roles])
def review_return(
    return_id: int,
    data: TaxReturnReview,
    current_user: CurrentUser,
    session: SessionDep,
) -> TaxReturnRead:
    tax_return = _get_accessible_return(return_id, current_user, session)
    tax_return.score = data.score
    tax_return.teacher_feedback = data.teacher_feedback
    session.add(tax_return)
    session.commit()
    session.refresh(tax_return)
    return return_to_read(session, tax_return)
