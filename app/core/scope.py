"""Tenant/class/student scope resolution shared by every document module.

Both e-Bupot slips and e-Faktur invoices belong to a (tenant, class, siswa)
triple and are written by siswa, guru, or admin on a siswa's behalf. The rules
for who may write what are identical, so they live here rather than being
copied per module. ``label`` only shapes the error message ("BP23", "Faktur
Pajak Keluaran") — the logic is the same for every document type.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models.enrollment import Enrollment
from app.models.enums import Role
from app.models.school_class import SchoolClass
from app.models.user import User


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


def resolve_document_scope(
    *,
    session: Session,
    current_user: User,
    class_id: int | None,
    siswa_id: int | None,
    label: str,
) -> tuple[int, int | None, int]:
    """Resolve and validate tenant/class/student scope for document writes."""
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
