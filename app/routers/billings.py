"""Simulated kode billing routes — the Pembayaran menu.

A billing is minted by the SPT submit flow; here students list, inspect, and
(paid) settle them. Paying an unpaid billing completes its SPT: the return
moves to ``reported`` and every source document is flagged
``reported_in_spt`` — the same effect "pembayaran" has in the real portal.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, func, select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.models.billing import BillingCode
from app.models.enums import BillingStatus, Role, TaxReturnStatus
from app.models.school_class import SchoolClass
from app.models.tax_return import TaxReturn
from app.models.user import User
from app.schemas.tax_return import BillingListResponse, BillingRead

router = APIRouter(prefix="/billings", tags=["billings"])

_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru, Role.siswa))


def can_read_billing(session: Session, current_user: User, billing: BillingCode) -> bool:
    if current_user.role == Role.superadmin:
        return True
    if current_user.role == Role.admin:
        return billing.tenant_id == current_user.tenant_id
    if current_user.role == Role.siswa:
        return billing.siswa_id == current_user.id
    if current_user.role == Role.guru:
        if billing.spt_id is None:
            return False
        spt = session.get(TaxReturn, billing.spt_id)
        if spt is not None and spt.class_id is not None:
            school_class = session.get(SchoolClass, spt.class_id)
            return school_class is not None and school_class.guru_id == current_user.id
        return False
    return False


def apply_access_filters(query, current_user: User):
    if current_user.role == Role.superadmin:
        return query
    if current_user.role == Role.admin:
        return query.where(BillingCode.tenant_id == current_user.tenant_id)
    if current_user.role == Role.siswa:
        return query.where(BillingCode.siswa_id == current_user.id)

    taught_class_ids = select(SchoolClass.id).where(SchoolClass.guru_id == current_user.id)
    spt_ids = select(TaxReturn.id).where(TaxReturn.class_id.in_(taught_class_ids))
    return query.where(BillingCode.spt_id.in_(spt_ids))


def _get_accessible_billing(
    billing_id: int, current_user: CurrentUser, session: SessionDep
) -> BillingCode:
    billing = session.get(BillingCode, billing_id)
    if billing is None or not can_read_billing(session, current_user, billing):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Kode billing tidak ditemukan"
        )
    return billing


@router.get("", response_model=BillingListResponse, dependencies=[_roles])
def list_billings(
    current_user: CurrentUser,
    session: SessionDep,
    status_filter: Annotated[BillingStatus | None, Query(alias="status")] = None,
    spt_id: int | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> BillingListResponse:
    conditions = []
    if status_filter is not None:
        conditions.append(BillingCode.status == status_filter)
    if spt_id is not None:
        conditions.append(BillingCode.spt_id == spt_id)

    base_count = apply_access_filters(
        select(func.count()).select_from(BillingCode).where(*conditions),
        current_user,
    )
    base_items = apply_access_filters(
        select(BillingCode)
        .where(*conditions)
        .order_by(BillingCode.id.desc())
        .offset((page - 1) * size)
        .limit(size),
        current_user,
    )
    total = session.exec(base_count).one()
    items = session.exec(base_items).all()
    return BillingListResponse(
        items=[BillingRead.model_validate(item.model_dump()) for item in items],
        total=total,
    )


@router.get("/{billing_id}", response_model=BillingRead, dependencies=[_roles])
def get_billing(
    billing_id: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> BillingRead:
    billing = _get_accessible_billing(billing_id, current_user, session)
    return BillingRead.model_validate(billing.model_dump())


@router.post("/{billing_id}/pay", response_model=BillingRead, dependencies=[_roles])
def pay_billing(
    billing_id: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> BillingRead:
    """Simulate settling the billing and report the linked SPT."""
    billing = _get_accessible_billing(billing_id, current_user, session)
    if billing.status == BillingStatus.paid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Kode billing sudah dibayar",
        )

    billing.status = BillingStatus.paid
    billing.paid_at = datetime.now(tz=UTC)
    session.add(billing)

    if billing.spt_id is not None:
        tax_return = session.get(TaxReturn, billing.spt_id)
        if tax_return is not None and tax_return.status == TaxReturnStatus.awaiting_payment:
            from app.services import spt as spt_service

            spt_service.mark_sources_reported(session, tax_return)
            tax_return.status = TaxReturnStatus.reported
            tax_return.submitted_at = datetime.now(tz=UTC)
            tax_return.submitted_by_id = current_user.id
            session.add(tax_return)

    session.commit()
    session.refresh(billing)
    return BillingRead.model_validate(billing.model_dump())
