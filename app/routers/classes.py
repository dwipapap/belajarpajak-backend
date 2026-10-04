"""Class & enrollment routes (admin, guru, siswa — tenant-scoped)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import func, select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.models.enrollment import Enrollment
from app.models.enums import Role
from app.models.school_class import SchoolClass
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.school_class import (
    ClassArchiveUpdate,
    ClassCreate,
    ClassDetail,
    ClassRead,
    EnrollmentCreate,
)
from app.schemas.user import UserRead
from app.services.audit import log_action

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
def list_classes(current_user: CurrentUser, session: SessionDep) -> list[ClassRead]:
    """Admin: all classes in tenant. Guru: own classes. Siswa: enrolled classes."""
    query = select(SchoolClass).where(SchoolClass.tenant_id == current_user.tenant_id)
    if current_user.role == Role.guru:
        query = query.where(SchoolClass.guru_id == current_user.id)
    elif current_user.role == Role.siswa:
        query = query.join(Enrollment, Enrollment.class_id == SchoolClass.id).where(
            Enrollment.siswa_id == current_user.id
        )
    
    classes = session.exec(query.order_by(SchoolClass.id)).all()
    results = []
    for c in classes:
        guru = session.get(User, c.guru_id)
        results.append(
            ClassRead(
                id=c.id,
                tenant_id=c.tenant_id,
                name=c.name,
                academic_year=c.academic_year,
                archived=c.archived,
                guru_id=c.guru_id,
                guru=UserRead.model_validate(guru) if guru else None,
            )
        )
    return results


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
    tenant = session.get(Tenant, current_user.tenant_id)
    if tenant is not None and tenant.max_kelas is not None:
        existing = session.exec(
            select(func.count())
            .select_from(SchoolClass)
            .where(SchoolClass.tenant_id == current_user.tenant_id)
        ).one()
        if existing >= tenant.max_kelas:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Kuota kelas instansi penuh (maksimal {tenant.max_kelas})",
            )
    school_class = SchoolClass(
        tenant_id=current_user.tenant_id,
        name=data.name,
        academic_year=data.academic_year,
        guru_id=data.guru_id,
    )
    session.add(school_class)
    session.flush()
    log_action(
        session,
        actor=current_user,
        action="class.create",
        entity="class",
        entity_id=school_class.id,
        tenant_id=current_user.tenant_id,
        summary=f"Membuat kelas {data.name} ({data.academic_year})",
    )
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
        log_action(
            session,
            actor=current_user,
            action="enrollment.add",
            entity="class",
            entity_id=class_id,
            tenant_id=current_user.tenant_id,
            summary=f"Memasukkan {siswa.full_name} ke kelas {school_class.name}",
        )
        session.commit()

    return _build_class_detail(session, school_class)


@router.delete(
    "/{class_id}/enrollments/{siswa_id}",
    response_model=ClassDetail,
    dependencies=[_admin_only],
)
def unenroll_siswa(
    class_id: int, siswa_id: int, current_user: CurrentUser, session: SessionDep
) -> ClassDetail:
    """Keluarkan siswa dari kelas — juga langkah pertama pindah kelas."""
    school_class = session.get(SchoolClass, class_id)
    if school_class is None or school_class.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kelas tidak ditemukan")

    enrollment = session.exec(
        select(Enrollment).where(
            Enrollment.class_id == class_id, Enrollment.siswa_id == siswa_id
        )
    ).first()
    if enrollment is not None:
        siswa = session.get(User, siswa_id)
        session.delete(enrollment)
        log_action(
            session,
            actor=current_user,
            action="enrollment.remove",
            entity="class",
            entity_id=class_id,
            tenant_id=current_user.tenant_id,
            summary=(
                f"Mengeluarkan {siswa.full_name if siswa else siswa_id} "
                f"dari kelas {school_class.name}"
            ),
        )
        session.commit()

    return _build_class_detail(session, school_class)


@router.patch("/{class_id}/archive", response_model=ClassRead, dependencies=[_admin_only])
def set_class_archived(
    class_id: int, data: ClassArchiveUpdate, current_user: CurrentUser, session: SessionDep
) -> SchoolClass:
    """Arsipkan/buka arsip kelas — riwayat dokumen tetap terbaca."""
    school_class = session.get(SchoolClass, class_id)
    if school_class is None or school_class.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kelas tidak ditemukan")

    school_class.archived = data.archived
    session.add(school_class)
    log_action(
        session,
        actor=current_user,
        action="class.archive" if data.archived else "class.unarchive",
        entity="class",
        entity_id=class_id,
        tenant_id=current_user.tenant_id,
        summary=f"{'Arsipkan' if data.archived else 'Buka arsip'} kelas {school_class.name}",
    )
    session.commit()
    session.refresh(school_class)
    return school_class


@router.get("/{class_id}", response_model=ClassDetail, dependencies=[_admin_guru_siswa])
def get_class(class_id: int, current_user: CurrentUser, session: SessionDep) -> ClassDetail:
    school_class = session.get(SchoolClass, class_id)
    # Everyone is tenant-scoped here; class browsing is institution-local.
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
        archived=school_class.archived,
        guru_id=school_class.guru_id,
        guru=UserRead.model_validate(guru) if guru else None,
        students=[UserRead.model_validate(s) for s in students],
    )
