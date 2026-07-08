"""Simulated e-Bupot BP21 routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import func, select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.models.bp21 import Bp21WithholdingSlip
from app.models.enums import Bp21Status, Role
from app.routers._slip_common import (
    apply_access_filters,
    calculate_dpp,
    calculate_income_tax,
    get_accessible_slip,
    issue_slip,
    percent_to_basis_points,
    resolve_slip_scope,
    slip_to_read,
)
from app.schemas.bp21 import (
    Bp21Create,
    Bp21Invalidate,
    Bp21ListResponse,
    Bp21Read,
    Bp21Review,
    Bp21Summary,
    Bp21Update,
)

router = APIRouter(prefix="/bp21", tags=["bp21"])

_bp21_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru, Role.siswa))
_bp21_review_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru))

LABEL = "BP21"


def _to_read(slip: Bp21WithholdingSlip) -> Bp21Read:
    return slip_to_read(Bp21Read, slip)


def _get_accessible_slip(
    slip_id: int, current_user: CurrentUser, session: SessionDep
) -> Bp21WithholdingSlip:
    return get_accessible_slip(Bp21WithholdingSlip, slip_id, current_user, session, LABEL)


def _apply_access_filters(query, current_user):
    return apply_access_filters(query, current_user, Bp21WithholdingSlip)


@router.get("", response_model=Bp21ListResponse, dependencies=[_bp21_roles])
def list_bp21(
    current_user: CurrentUser,
    session: SessionDep,
    status_filter: Annotated[Bp21Status | None, Query(alias="status")] = None,
    tenant_id: int | None = None,
    class_id: int | None = None,
    siswa_id: int | None = None,
    tax_year: Annotated[int | None, Query(ge=2020, le=2100)] = None,
    tax_month: Annotated[int | None, Query(ge=1, le=12)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Bp21ListResponse:
    conditions = []
    if status_filter is not None:
        conditions.append(Bp21WithholdingSlip.status == status_filter)
    if current_user.role == Role.superadmin and tenant_id is not None:
        conditions.append(Bp21WithholdingSlip.tenant_id == tenant_id)
    if class_id is not None:
        conditions.append(Bp21WithholdingSlip.class_id == class_id)
    if siswa_id is not None and current_user.role != Role.siswa:
        conditions.append(Bp21WithholdingSlip.siswa_id == siswa_id)
    if tax_year is not None:
        conditions.append(Bp21WithholdingSlip.tax_year == tax_year)
    if tax_month is not None:
        conditions.append(Bp21WithholdingSlip.tax_month == tax_month)

    base_count = _apply_access_filters(
        select(func.count()).select_from(Bp21WithholdingSlip).where(*conditions),
        current_user,
    )
    base_items = _apply_access_filters(
        select(Bp21WithholdingSlip)
        .where(*conditions)
        .order_by(Bp21WithholdingSlip.id.desc())
        .offset((page - 1) * size)
        .limit(size),
        current_user,
    )
    total = session.exec(base_count).one()
    items = session.exec(base_items).all()
    return Bp21ListResponse(
        items=[_to_read(item) for item in items],
        total=total,
        page=page,
        size=size,
    )


@router.get("/summary", response_model=Bp21Summary, dependencies=[_bp21_roles])
def bp21_summary(current_user: CurrentUser, session: SessionDep) -> Bp21Summary:
    query = _apply_access_filters(
        select(Bp21WithholdingSlip.status, func.count())
        .select_from(Bp21WithholdingSlip)
        .group_by(Bp21WithholdingSlip.status),
        current_user,
    )
    counts = dict(session.exec(query).all())
    return Bp21Summary(
        draft=counts.get(Bp21Status.draft, 0),
        issued=counts.get(Bp21Status.issued, 0),
        invalid=counts.get(Bp21Status.invalid, 0),
        total=sum(counts.values()),
    )


@router.post(
    "",
    response_model=Bp21Read,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_bp21_roles],
)
def create_bp21(data: Bp21Create, current_user: CurrentUser, session: SessionDep) -> Bp21Read:
    tenant_id, class_id, siswa_id = resolve_slip_scope(
        session=session,
        current_user=current_user,
        class_id=data.class_id,
        siswa_id=data.siswa_id,
        label=LABEL,
    )
    rate_basis_points = percent_to_basis_points(data.rate_percent)
    dpp_rate_basis_points = percent_to_basis_points(data.dpp_percent)
    dpp = calculate_dpp(data.gross_income, dpp_rate_basis_points)
    slip = Bp21WithholdingSlip(
        tenant_id=tenant_id,
        class_id=class_id,
        siswa_id=siswa_id,
        created_by_id=current_user.id,
        tax_month=data.tax_month,
        tax_year=data.tax_year,
        withholder_npwp=data.withholder_npwp,
        withholder_name=data.withholder_name,
        withholder_nitku=data.withholder_nitku,
        recipient_identity_number=data.recipient_identity_number,
        recipient_name=data.recipient_name,
        recipient_address=data.recipient_address,
        recipient_nitku=data.recipient_nitku,
        ptkp_status=data.ptkp_status,
        tax_object_code=data.tax_object_code,
        income_type=data.income_type,
        tax_nature=data.tax_nature,
        tax_facility=data.tax_facility,
        previous_gross_income=data.previous_gross_income,
        gross_income=data.gross_income,
        dpp=dpp,
        dpp_rate_basis_points=dpp_rate_basis_points,
        rate_basis_points=rate_basis_points,
        income_tax=calculate_income_tax(dpp, rate_basis_points, data.tax_facility),
        kap_kjs=data.kap_kjs,
        document_type=data.document_type,
        document_number=data.document_number,
        document_date=data.document_date,
        document_nitku=data.document_nitku,
    )
    session.add(slip)
    session.commit()
    session.refresh(slip)
    return _to_read(slip)


@router.get("/{slip_id}", response_model=Bp21Read, dependencies=[_bp21_roles])
def get_bp21(slip_id: int, current_user: CurrentUser, session: SessionDep) -> Bp21Read:
    return _to_read(_get_accessible_slip(slip_id, current_user, session))


@router.patch("/{slip_id}", response_model=Bp21Read, dependencies=[_bp21_roles])
def update_bp21(
    slip_id: int, data: Bp21Update, current_user: CurrentUser, session: SessionDep
) -> Bp21Read:
    slip = _get_accessible_slip(slip_id, current_user, session)
    if slip.status != Bp21Status.draft:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="BP21 yang sudah diterbitkan atau tidak valid tidak dapat diubah",
        )

    payload = data.model_dump(exclude_unset=True)
    class_id = payload.get("class_id", slip.class_id)
    siswa_id = payload.get("siswa_id", slip.siswa_id)
    tenant_id, resolved_class_id, resolved_siswa_id = resolve_slip_scope(
        session=session,
        current_user=current_user,
        class_id=class_id,
        siswa_id=siswa_id,
        label=LABEL,
    )
    slip.tenant_id = tenant_id
    slip.class_id = resolved_class_id
    slip.siswa_id = resolved_siswa_id

    if "rate_percent" in payload:
        slip.rate_basis_points = percent_to_basis_points(payload.pop("rate_percent"))
    if "dpp_percent" in payload:
        slip.dpp_rate_basis_points = percent_to_basis_points(payload.pop("dpp_percent"))
    for field, value in payload.items():
        if field in {"class_id", "siswa_id"}:
            continue
        setattr(slip, field, value)
    slip.dpp = calculate_dpp(slip.gross_income, slip.dpp_rate_basis_points)
    slip.income_tax = calculate_income_tax(slip.dpp, slip.rate_basis_points, slip.tax_facility)
    session.add(slip)
    session.commit()
    session.refresh(slip)
    return _to_read(slip)


@router.post("/{slip_id}/issue", response_model=Bp21Read, dependencies=[_bp21_roles])
def issue_bp21(slip_id: int, current_user: CurrentUser, session: SessionDep) -> Bp21Read:
    slip = _get_accessible_slip(slip_id, current_user, session)
    if slip.status != Bp21Status.draft:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="BP21 bukan draft")

    issue_slip(slip, LABEL)
    session.add(slip)
    session.commit()
    session.refresh(slip)
    return _to_read(slip)


@router.post(
    "/{slip_id}/invalidate",
    response_model=Bp21Read,
    dependencies=[_bp21_review_roles],
)
def invalidate_bp21(
    slip_id: int, data: Bp21Invalidate, current_user: CurrentUser, session: SessionDep
) -> Bp21Read:
    slip = _get_accessible_slip(slip_id, current_user, session)
    slip.status = Bp21Status.invalid
    slip.invalid_reason = data.invalid_reason
    session.add(slip)
    session.commit()
    session.refresh(slip)
    return _to_read(slip)


@router.patch("/{slip_id}/review", response_model=Bp21Read, dependencies=[_bp21_review_roles])
def review_bp21(
    slip_id: int, data: Bp21Review, current_user: CurrentUser, session: SessionDep
) -> Bp21Read:
    slip = _get_accessible_slip(slip_id, current_user, session)
    payload = data.model_dump(exclude_unset=True)
    for field, value in payload.items():
        setattr(slip, field, value)
    session.add(slip)
    session.commit()
    session.refresh(slip)
    return _to_read(slip)
