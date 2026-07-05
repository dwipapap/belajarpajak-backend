"""Simulated e-Bupot BP21 routes."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import func, select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.models.bp21 import Bp21WithholdingSlip
from app.models.enrollment import Enrollment
from app.models.enums import Bp21Status, Bp21TaxFacility, Role
from app.models.school_class import SchoolClass
from app.models.user import User
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


def _rate_to_basis_points(rate_percent: float) -> int:
    rate = Decimal(str(rate_percent))
    return int((rate * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _basis_points_to_rate(rate_basis_points: int) -> float:
    rate = (Decimal(rate_basis_points) / Decimal("100")).quantize(Decimal("0.01"))
    return float(rate)


def _calculate_income_tax(dpp: int, rate_basis_points: int, facility: Bp21TaxFacility) -> int:
    if facility in (Bp21TaxFacility.skb, Bp21TaxFacility.rate_0):
        return 0
    tax = Decimal(dpp) * Decimal(rate_basis_points) / Decimal("10000")
    return int(tax.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _to_read(slip: Bp21WithholdingSlip) -> Bp21Read:
    return Bp21Read(
        id=slip.id,
        tenant_id=slip.tenant_id,
        class_id=slip.class_id,
        siswa_id=slip.siswa_id,
        created_by_id=slip.created_by_id,
        status=slip.status,
        withholding_number=slip.withholding_number,
        issued_at=slip.issued_at,
        invalid_reason=slip.invalid_reason,
        tax_month=slip.tax_month,
        tax_year=slip.tax_year,
        electronic_signature_status=slip.electronic_signature_status,
        withholder_npwp=slip.withholder_npwp,
        withholder_name=slip.withholder_name,
        withholder_nitku=slip.withholder_nitku,
        recipient_identity_number=slip.recipient_identity_number,
        recipient_name=slip.recipient_name,
        recipient_address=slip.recipient_address,
        tax_object_code=slip.tax_object_code,
        income_type=slip.income_type,
        tax_nature=slip.tax_nature,
        tax_facility=slip.tax_facility,
        dpp=slip.dpp,
        rate_percent=_basis_points_to_rate(slip.rate_basis_points),
        income_tax=slip.income_tax,
        score=slip.score,
        teacher_feedback=slip.teacher_feedback,
        created_at=slip.created_at,
        updated_at=slip.updated_at,
    )


def _is_enrolled(session: SessionDep, *, class_id: int, siswa_id: int) -> bool:
    return (
        session.exec(
            select(Enrollment).where(
                Enrollment.class_id == class_id,
                Enrollment.siswa_id == siswa_id,
            )
        ).first()
        is not None
    )


def _resolve_bp21_scope(
    *,
    session: SessionDep,
    current_user: User,
    class_id: int | None,
    siswa_id: int | None,
) -> tuple[int, int | None, int]:
    """Resolve and validate tenant/class/student scope for BP21 writes."""
    if current_user.role == Role.siswa:
        siswa_id = current_user.id

    if siswa_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="siswa_id wajib untuk membuat BP21 selain dari akun siswa",
        )

    siswa = session.get(User, siswa_id)
    if siswa is None or siswa.role != Role.siswa:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="siswa_id harus merujuk ke akun siswa",
        )

    if (
        current_user.role not in (Role.superadmin, Role.siswa)
        and siswa.tenant_id != current_user.tenant_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses lintas tenant ditolak",
        )

    tenant_id = siswa.tenant_id
    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Siswa wajib berada dalam tenant",
        )

    if class_id is not None:
        school_class = session.get(SchoolClass, class_id)
        if school_class is None or school_class.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Kelas tidak ditemukan",
            )
        if current_user.role == Role.guru and school_class.guru_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bukan kelas Anda")
        if current_user.role == Role.siswa and not _is_enrolled(
            session, class_id=class_id, siswa_id=current_user.id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Anda tidak terdaftar di kelas ini",
            )
        if not _is_enrolled(session, class_id=class_id, siswa_id=siswa_id):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Siswa belum terdaftar pada kelas ini",
            )
    elif current_user.role == Role.guru:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Guru wajib memilih kelas untuk BP21",
        )

    return tenant_id, class_id, siswa_id


def _can_read_slip(session: SessionDep, current_user: User, slip: Bp21WithholdingSlip) -> bool:
    if current_user.role == Role.superadmin:
        return True
    if current_user.role == Role.admin:
        return slip.tenant_id == current_user.tenant_id
    if current_user.role == Role.siswa:
        return slip.siswa_id == current_user.id
    if current_user.role == Role.guru and slip.class_id is not None:
        school_class = session.get(SchoolClass, slip.class_id)
        return school_class is not None and school_class.guru_id == current_user.id
    return False


def _get_accessible_slip(
    slip_id: int, current_user: CurrentUser, session: SessionDep
) -> Bp21WithholdingSlip:
    slip = session.get(Bp21WithholdingSlip, slip_id)
    if slip is None or not _can_read_slip(session, current_user, slip):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="BP21 tidak ditemukan")
    return slip


def _apply_access_filters(query, current_user: User):
    if current_user.role == Role.superadmin:
        return query
    if current_user.role == Role.admin:
        return query.where(Bp21WithholdingSlip.tenant_id == current_user.tenant_id)
    if current_user.role == Role.siswa:
        return query.where(Bp21WithholdingSlip.siswa_id == current_user.id)

    taught_class_ids = select(SchoolClass.id).where(SchoolClass.guru_id == current_user.id)
    return query.where(Bp21WithholdingSlip.class_id.in_(taught_class_ids))


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
    query = _apply_access_filters(select(Bp21WithholdingSlip.status), current_user)
    statuses = session.exec(query).all()
    draft = sum(1 for item in statuses if item == Bp21Status.draft)
    issued = sum(1 for item in statuses if item == Bp21Status.issued)
    invalid = sum(1 for item in statuses if item == Bp21Status.invalid)
    return Bp21Summary(draft=draft, issued=issued, invalid=invalid, total=len(statuses))


@router.post(
    "",
    response_model=Bp21Read,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_bp21_roles],
)
def create_bp21(data: Bp21Create, current_user: CurrentUser, session: SessionDep) -> Bp21Read:
    tenant_id, class_id, siswa_id = _resolve_bp21_scope(
        session=session,
        current_user=current_user,
        class_id=data.class_id,
        siswa_id=data.siswa_id,
    )
    rate_basis_points = _rate_to_basis_points(data.rate_percent)
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
        tax_object_code=data.tax_object_code,
        income_type=data.income_type,
        tax_nature=data.tax_nature,
        tax_facility=data.tax_facility,
        dpp=data.dpp,
        rate_basis_points=rate_basis_points,
        income_tax=_calculate_income_tax(data.dpp, rate_basis_points, data.tax_facility),
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
    tenant_id, resolved_class_id, resolved_siswa_id = _resolve_bp21_scope(
        session=session,
        current_user=current_user,
        class_id=class_id,
        siswa_id=siswa_id,
    )
    slip.tenant_id = tenant_id
    slip.class_id = resolved_class_id
    slip.siswa_id = resolved_siswa_id

    if "rate_percent" in payload:
        slip.rate_basis_points = _rate_to_basis_points(payload.pop("rate_percent"))
    for field, value in payload.items():
        if field in {"class_id", "siswa_id"}:
            continue
        setattr(slip, field, value)
    slip.income_tax = _calculate_income_tax(slip.dpp, slip.rate_basis_points, slip.tax_facility)
    session.add(slip)
    session.commit()
    session.refresh(slip)
    return _to_read(slip)


@router.post("/{slip_id}/issue", response_model=Bp21Read, dependencies=[_bp21_roles])
def issue_bp21(slip_id: int, current_user: CurrentUser, session: SessionDep) -> Bp21Read:
    slip = _get_accessible_slip(slip_id, current_user, session)
    if slip.status != Bp21Status.draft:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="BP21 bukan draft")

    slip.status = Bp21Status.issued
    slip.issued_at = datetime.now(UTC)
    slip.electronic_signature_status = "signed"
    slip.withholding_number = f"BP21-{slip.tax_year}{slip.tax_month:02d}-{slip.id:06d}"
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
