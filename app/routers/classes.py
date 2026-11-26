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
    BulkEnrollmentCreate,
    ClassArchiveUpdate,
    ClassCreate,
    ClassDetail,
    ClassRead,
    ClassUpdate,
    EnrollmentCreate,
)
from app.schemas.user import UserRead
from app.services.audit import log_action

router = APIRouter(prefix="/classes", tags=["classes"])

_admin_guru_siswa = Depends(require_roles(Role.admin, Role.guru, Role.siswa))
_read_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru, Role.siswa))
_admin_guru = Depends(require_roles(Role.admin, Role.guru))
_admin_or_super = Depends(require_roles(Role.superadmin, Role.admin))


def _enrolled_students(session: SessionDep, class_id: int) -> list[User]:
    return session.exec(
        select(User)
        .join(Enrollment, Enrollment.siswa_id == User.id)
        .where(Enrollment.class_id == class_id)
        .order_by(User.full_name)
    ).all()


def _get_class_for_write(
    session: SessionDep, class_id: int, current_user: User
) -> SchoolClass:
    school_class = session.get(SchoolClass, class_id)
    if school_class is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kelas tidak ditemukan")
    if current_user.role == Role.admin and school_class.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kelas tidak ditemukan")
    return school_class


def _build_class_read(
    session: SessionDep, school_class: SchoolClass, *, student_count: int | None = None
) -> ClassRead:
    guru = session.get(User, school_class.guru_id)
    if student_count is None:
        student_count = session.exec(
            select(func.count())
            .select_from(Enrollment)
            .where(Enrollment.class_id == school_class.id)
        ).one()
    return ClassRead(
        id=school_class.id,
        tenant_id=school_class.tenant_id,
        name=school_class.name,
        academic_year=school_class.academic_year,
        archived=school_class.archived,
        guru_id=school_class.guru_id,
        guru=UserRead.model_validate(guru) if guru else None,
        student_count=int(student_count),
    )


@router.get("", response_model=list[ClassRead], dependencies=[_read_roles])
def list_classes(
    current_user: CurrentUser,
    session: SessionDep,
    tenant_id: int | None = None,
) -> list[ClassRead]:
    """Admin: all classes in tenant. Guru: own classes. Siswa: enrolled classes.
    Superadmin: all classes cross-tenant, optionally filtered by tenant_id."""
    query = select(SchoolClass)
    if current_user.role != Role.superadmin:
        query = query.where(SchoolClass.tenant_id == current_user.tenant_id)
    elif tenant_id is not None:
        query = query.where(SchoolClass.tenant_id == tenant_id)
    if current_user.role == Role.guru:
        query = query.where(SchoolClass.guru_id == current_user.id)
    elif current_user.role == Role.siswa:
        query = query.join(Enrollment, Enrollment.class_id == SchoolClass.id).where(
            Enrollment.siswa_id == current_user.id
        )

    classes = session.exec(query.order_by(SchoolClass.id)).all()
    class_ids = [c.id for c in classes if c.id is not None]
    counts: dict[int, int] = {}
    if class_ids:
        for cid, n in session.exec(
            select(Enrollment.class_id, func.count())
            .where(Enrollment.class_id.in_(class_ids))
            .group_by(Enrollment.class_id)
        ).all():
            counts[cid] = int(n)

    return [_build_class_read(session, c, student_count=counts.get(c.id, 0)) for c in classes]


@router.post(
    "",
    response_model=ClassRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_admin_or_super],
)
def create_class(
    data: ClassCreate, current_user: CurrentUser, session: SessionDep
) -> ClassRead:
    guru = session.get(User, data.guru_id)
    if guru is None or guru.role != Role.guru:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="guru_id harus merujuk ke guru yang valid",
        )
    if current_user.role == Role.admin:
        target_tenant_id = current_user.tenant_id
        if guru.tenant_id != target_tenant_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="guru_id harus merujuk ke guru pada tenant Anda",
            )
    else:
        target_tenant_id = data.tenant_id if data.tenant_id is not None else guru.tenant_id
        if guru.tenant_id != target_tenant_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="guru_id harus merujuk ke guru pada tenant yang dipilih",
            )

    tenant = session.get(Tenant, target_tenant_id)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="tenant_id tidak valid",
        )
    if tenant.max_kelas is not None:
        existing = session.exec(
            select(func.count())
            .select_from(SchoolClass)
            .where(SchoolClass.tenant_id == target_tenant_id)
        ).one()
        if existing >= tenant.max_kelas:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Kuota kelas instansi penuh (maksimal {tenant.max_kelas})",
            )
    school_class = SchoolClass(
        tenant_id=target_tenant_id,
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
        tenant_id=target_tenant_id,
        summary=f"Membuat kelas {data.name} ({data.academic_year})",
    )
    session.commit()
    session.refresh(school_class)
    return _build_class_read(session, school_class, student_count=0)


@router.patch("/{class_id}", response_model=ClassRead, dependencies=[_admin_or_super])
def update_class(
    class_id: int, data: ClassUpdate, current_user: CurrentUser, session: SessionDep
) -> ClassRead:
    school_class = _get_class_for_write(session, class_id, current_user)
    payload = data.model_dump(exclude_unset=True)

    for required_field in ("name", "academic_year", "guru_id"):
        if required_field in payload and payload[required_field] is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{required_field} tidak boleh kosong",
            )

    if "guru_id" in payload:
        guru = session.get(User, payload["guru_id"])
        if guru is None or guru.role != Role.guru or guru.tenant_id != school_class.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="guru_id harus merujuk ke guru pada tenant kelas ini",
            )

    for field, value in payload.items():
        setattr(school_class, field, value)
    session.add(school_class)
    log_action(
        session,
        actor=current_user,
        action="class.update",
        entity="class",
        entity_id=school_class.id,
        tenant_id=school_class.tenant_id,
        summary=f"Memperbarui kelas {school_class.name} ({school_class.academic_year})",
    )
    session.commit()
    session.refresh(school_class)
    return _build_class_read(session, school_class)


@router.post(
    "/{class_id}/enrollments",
    response_model=ClassDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_admin_or_super],
)
def enroll_siswa(
    class_id: int, data: EnrollmentCreate, current_user: CurrentUser, session: SessionDep
) -> ClassDetail:
    school_class = _get_class_for_write(session, class_id, current_user)

    siswa = session.get(User, data.siswa_id)
    if (
        siswa is None
        or siswa.role != Role.siswa
        or siswa.tenant_id != school_class.tenant_id
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="siswa_id harus merujuk ke siswa pada tenant yang sama",
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
            tenant_id=school_class.tenant_id,
            summary=f"Memasukkan {siswa.full_name} ke kelas {school_class.name}",
        )
        session.commit()

    return _build_class_detail(session, school_class)


@router.post(
    "/{class_id}/enrollments/bulk",
    response_model=ClassDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_admin_or_super],
)
def bulk_enroll_siswa(
    class_id: int, data: BulkEnrollmentCreate, current_user: CurrentUser, session: SessionDep
) -> ClassDetail:
    school_class = _get_class_for_write(session, class_id, current_user)
    unique_ids = list(dict.fromkeys(data.siswa_ids))

    students = session.exec(select(User).where(User.id.in_(unique_ids))).all()
    students_by_id = {s.id: s for s in students}
    invalid = [
        sid
        for sid in unique_ids
        if sid not in students_by_id
        or students_by_id[sid].role != Role.siswa
        or students_by_id[sid].tenant_id != school_class.tenant_id
    ]
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Semua siswa_id harus merujuk ke siswa pada tenant yang sama",
        )

    existing_ids = set(
        session.exec(
            select(Enrollment.siswa_id).where(
                Enrollment.class_id == class_id, Enrollment.siswa_id.in_(unique_ids)
            )
        ).all()
    )
    added = 0
    for sid in unique_ids:
        if sid not in existing_ids:
            session.add(Enrollment(class_id=class_id, siswa_id=sid))
            added += 1

    log_action(
        session,
        actor=current_user,
        action="enrollment.bulk_add",
        entity="class",
        entity_id=class_id,
        tenant_id=school_class.tenant_id,
        summary=f"Memasukkan {added} siswa ke kelas {school_class.name}",
    )
    session.commit()
    return _build_class_detail(session, school_class)


@router.delete(
    "/{class_id}/enrollments/{siswa_id}",
    response_model=ClassDetail,
    dependencies=[_admin_or_super],
)
def unenroll_siswa(
    class_id: int, siswa_id: int, current_user: CurrentUser, session: SessionDep
) -> ClassDetail:
    """Keluarkan siswa dari kelas — juga langkah pertama pindah kelas."""
    school_class = _get_class_for_write(session, class_id, current_user)

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
            tenant_id=school_class.tenant_id,
            summary=(
                f"Mengeluarkan {siswa.full_name if siswa else siswa_id} "
                f"dari kelas {school_class.name}"
            ),
        )
        session.commit()

    return _build_class_detail(session, school_class)


@router.patch("/{class_id}/archive", response_model=ClassRead, dependencies=[_admin_or_super])
def set_class_archived(
    class_id: int, data: ClassArchiveUpdate, current_user: CurrentUser, session: SessionDep
) -> ClassRead:
    """Arsipkan/buka arsip kelas — riwayat dokumen tetap terbaca."""
    school_class = _get_class_for_write(session, class_id, current_user)

    school_class.archived = data.archived
    session.add(school_class)
    log_action(
        session,
        actor=current_user,
        action="class.archive" if data.archived else "class.unarchive",
        entity="class",
        entity_id=class_id,
        tenant_id=school_class.tenant_id,
        summary=f"{'Arsipkan' if data.archived else 'Buka arsip'} kelas {school_class.name}",
    )
    session.commit()
    session.refresh(school_class)
    return _build_class_read(session, school_class)


@router.get("/{class_id}", response_model=ClassDetail, dependencies=[_read_roles])
def get_class(class_id: int, current_user: CurrentUser, session: SessionDep) -> ClassDetail:
    school_class = session.get(SchoolClass, class_id)
    # Superadmin reads cross-tenant; everyone else is tenant-scoped here.
    if school_class is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kelas tidak ditemukan")
    if current_user.role == Role.superadmin:
        return _build_class_detail(session, school_class)
    if school_class.tenant_id != current_user.tenant_id:
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
        student_count=len(students),
        students=[UserRead.model_validate(s) for s in students],
    )
