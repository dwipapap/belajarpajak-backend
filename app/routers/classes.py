"""Class & enrollment routes (admin, guru, siswa — tenant-scoped)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.models.enrollment import Enrollment
from app.models.enums import Role
from app.models.school_class import SchoolClass
from app.models.user import User
from app.schemas.school_class import ClassCreate, ClassDetail, ClassRead, EnrollmentCreate
from app.schemas.user import UserRead

router = APIRouter(prefix="/classes", tags=["classes"])

_admin_guru_siswa = Depends(require_roles(Role.admin, Role.guru, Role.siswa))
_admin_guru = Depends(require_roles(Role.admin, Role.guru))
_admin_only = Depends(require_roles(Role.admin))


def _enrolled_students(session: SessionDep, class_id: int) -> list[User]:
    return session.exec(
        select(User)
        .join(Enrollment, Enrollment.siswa_id == User.id)
        .where(Enrollment.class_id == class_id)
        .order_by(User.full_name)
    ).all()


@router.get("", response_model=list[ClassRead], dependencies=[_admin_guru_siswa])
def list_classes(current_user: CurrentUser, session: SessionDep) -> list[SchoolClass]:
    """Admin: all classes in tenant. Guru: own classes. Siswa: enrolled classes."""
    query = select(SchoolClass).where(SchoolClass.tenant_id == current_user.tenant_id)
    if current_user.role == Role.guru:
        query = query.where(SchoolClass.guru_id == current_user.id)
    elif current_user.role == Role.siswa:
        query = query.join(Enrollment, Enrollment.class_id == SchoolClass.id).where(
            Enrollment.siswa_id == current_user.id
        )
    return session.exec(query.order_by(SchoolClass.id)).all()


@router.post(
    "",
    response_model=ClassRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_admin_only],
)
def create_class(data: ClassCreate, current_user: CurrentUser, session: SessionDep) -> SchoolClass:
    guru = session.get(User, data.guru_id)
    if (
        guru is None
        or guru.role != Role.guru
        or guru.tenant_id != current_user.tenant_id
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="guru_id harus merujuk ke guru pada tenant Anda",
        )
    school_class = SchoolClass(
        tenant_id=current_user.tenant_id,
        name=data.name,
        academic_year=data.academic_year,
        guru_id=data.guru_id,
    )
    session.add(school_class)
    session.commit()
    session.refresh(school_class)
    return school_class


@router.post(
    "/{class_id}/enrollments",
    response_model=ClassDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_admin_only],
)
def enroll_siswa(
    class_id: int, data: EnrollmentCreate, current_user: CurrentUser, session: SessionDep
) -> ClassDetail:
    school_class = session.get(SchoolClass, class_id)
    if school_class is None or school_class.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kelas tidak ditemukan")

    siswa = session.get(User, data.siswa_id)
    if (
        siswa is None
        or siswa.role != Role.siswa
        or siswa.tenant_id != current_user.tenant_id
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="siswa_id harus merujuk ke siswa pada tenant Anda",
        )

    existing = session.exec(
        select(Enrollment).where(
            Enrollment.class_id == class_id, Enrollment.siswa_id == data.siswa_id
        )
    ).first()
    if existing is None:
        session.add(Enrollment(class_id=class_id, siswa_id=data.siswa_id))
        session.commit()

    return _build_class_detail(session, school_class)


@router.get("/{class_id}", response_model=ClassDetail, dependencies=[_admin_guru_siswa])
def get_class(class_id: int, current_user: CurrentUser, session: SessionDep) -> ClassDetail:
    school_class = session.get(SchoolClass, class_id)
    # Everyone is tenant-scoped here (no superadmin route to classes in Phase 1).
    if school_class is None or school_class.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kelas tidak ditemukan")

    if current_user.role == Role.guru and school_class.guru_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bukan kelas Anda")

    if current_user.role == Role.siswa:
        enrolled = session.exec(
            select(Enrollment).where(
                Enrollment.class_id == class_id, Enrollment.siswa_id == current_user.id
            )
        ).first()
        if enrolled is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Anda tidak terdaftar di kelas ini"
            )

    return _build_class_detail(session, school_class)


def _build_class_detail(session: SessionDep, school_class: SchoolClass) -> ClassDetail:
    guru = session.get(User, school_class.guru_id)
    students = _enrolled_students(session, school_class.id)
    return ClassDetail(
        id=school_class.id,
        tenant_id=school_class.tenant_id,
        name=school_class.name,
        academic_year=school_class.academic_year,
        guru_id=school_class.guru_id,
        guru=UserRead.model_validate(guru) if guru else None,
        students=[UserRead.model_validate(s) for s in students],
    )
