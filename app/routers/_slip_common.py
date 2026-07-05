"""Shared helpers for the e-Bupot slip routers (BP21, BP26, ...).

Both slip models share the same lifecycle, money math, and access rules; the
helpers here are duck-typed over any model with the common columns
(tenant_id/class_id/siswa_id/status/...). Routers bind their model and label
("BP21", "BP26") via thin local wrappers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models.enrollment import Enrollment
from app.models.enums import Role, SlipStatus, SlipTaxFacility
from app.models.school_class import SchoolClass
from app.models.user import User


def percent_to_basis_points(percent: float) -> int:
    rate = Decimal(str(percent))
    return int((rate * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def basis_points_to_percent(basis_points: int) -> float:
    return float((Decimal(basis_points) / Decimal("100")).quantize(Decimal("0.01")))


def calculate_dpp(gross_income: int, dpp_rate_basis_points: int) -> int:
    value = Decimal(gross_income) * Decimal(dpp_rate_basis_points) / Decimal("10000")
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def calculate_income_tax(dpp: int, rate_basis_points: int, facility: SlipTaxFacility) -> int:
    if facility in (SlipTaxFacility.skb, SlipTaxFacility.rate_0):
        return 0
    tax = Decimal(dpp) * Decimal(rate_basis_points) / Decimal("10000")
    return int(tax.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def slip_to_read(read_cls, slip):
    """Map a slip row to its read schema; percent fields come from basis points."""
    return read_cls.model_validate(
        {
            **slip.model_dump(),
            "dpp_percent": basis_points_to_percent(slip.dpp_rate_basis_points),
            "rate_percent": basis_points_to_percent(slip.rate_basis_points),
        }
    )


def issue_slip(slip, label: str) -> None:
    slip.status = SlipStatus.issued
    slip.issued_at = datetime.now(UTC)
    slip.electronic_signature_status = "signed"
    slip.withholding_number = f"{label}-{slip.tax_year}{slip.tax_month:02d}-{slip.id:06d}"


def is_enrolled(session: Session, *, class_id: int, siswa_id: int) -> bool:
    return (
        session.exec(
            select(Enrollment).where(
                Enrollment.class_id == class_id,
                Enrollment.siswa_id == siswa_id,
            )
        ).first()
        is not None
    )


def resolve_slip_scope(
    *,
    session: Session,
    current_user: User,
    class_id: int | None,
    siswa_id: int | None,
    label: str,
) -> tuple[int, int | None, int]:
    """Resolve and validate tenant/class/student scope for slip writes."""
    if current_user.role == Role.siswa:
        siswa_id = current_user.id

    if siswa_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"siswa_id wajib untuk membuat {label} selain dari akun siswa",
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
        if current_user.role == Role.siswa and not is_enrolled(
            session, class_id=class_id, siswa_id=current_user.id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Anda tidak terdaftar di kelas ini",
            )
        if not is_enrolled(session, class_id=class_id, siswa_id=siswa_id):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Siswa belum terdaftar pada kelas ini",
            )
    elif current_user.role == Role.guru:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Guru wajib memilih kelas untuk {label}",
        )

    return tenant_id, class_id, siswa_id


def can_read_slip(session: Session, current_user: User, slip) -> bool:
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


def get_accessible_slip(model, slip_id: int, current_user: User, session: Session, label: str):
    slip = session.get(model, slip_id)
    if slip is None or not can_read_slip(session, current_user, slip):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} tidak ditemukan"
        )
    return slip


def apply_access_filters(query, current_user: User, model):
    if current_user.role == Role.superadmin:
        return query
    if current_user.role == Role.admin:
        return query.where(model.tenant_id == current_user.tenant_id)
    if current_user.role == Role.siswa:
        return query.where(model.siswa_id == current_user.id)

    taught_class_ids = select(SchoolClass.id).where(SchoolClass.guru_id == current_user.id)
    return query.where(model.class_id.in_(taught_class_ids))
