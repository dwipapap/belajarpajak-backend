"""Dashboard summary route — role-shaped counts."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import func, select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.models.enrollment import Enrollment
from app.models.enums import Role
from app.models.school_class import SchoolClass
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.dashboard import (
    DashboardSummary,
    GuruDashboard,
    PlatformDashboard,
    SiswaDashboard,
    TenantOverview,
)
from app.services.dashboard import (
    build_guru_dashboard,
    build_platform_dashboard,
    build_siswa_dashboard,
    build_tenant_overviews,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _count(session: SessionDep, statement) -> int:
    return session.exec(statement).one()


@router.get("/summary", response_model=DashboardSummary)
def summary(current_user: CurrentUser, session: SessionDep) -> DashboardSummary:
    role = current_user.role
    tid = current_user.tenant_id

    if role == Role.superadmin:
        return DashboardSummary(
            tenants=_count(session, select(func.count()).select_from(Tenant)),
            users=_count(session, select(func.count()).select_from(User)),
            classes=_count(session, select(func.count()).select_from(SchoolClass)),
        )

    if role == Role.admin:
        return DashboardSummary(
            guru=_count(
                session,
                select(func.count())
                .select_from(User)
                .where(User.tenant_id == tid, User.role == Role.guru),
            ),
            siswa=_count(
                session,
                select(func.count())
                .select_from(User)
                .where(User.tenant_id == tid, User.role == Role.siswa),
            ),
            classes=_count(
                session,
                select(func.count()).select_from(SchoolClass).where(SchoolClass.tenant_id == tid),
            ),
        )

    if role == Role.guru:
        classes = _count(
            session,
            select(func.count())
            .select_from(SchoolClass)
            .where(SchoolClass.guru_id == current_user.id),
        )
        siswa = _count(
            session,
            select(func.count(func.distinct(Enrollment.siswa_id)))
            .select_from(Enrollment)
            .join(SchoolClass, SchoolClass.id == Enrollment.class_id)
            .where(SchoolClass.guru_id == current_user.id),
        )
        return DashboardSummary(classes=classes, siswa=siswa)

    # siswa
    return DashboardSummary(
        classes=_count(
            session,
            select(func.count())
            .select_from(Enrollment)
            .where(Enrollment.siswa_id == current_user.id),
        )
    )


@router.get("/guru", response_model=GuruDashboard, dependencies=[Depends(require_roles(Role.guru))])
def guru_dashboard(
    current_user: CurrentUser, session: SessionDep, class_id: int | None = None
) -> GuruDashboard:
    """Per-class student progress + grading queue; only the guru's own classes."""
    data = build_guru_dashboard(session, current_user, class_id)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kelas tidak ditemukan")
    return data


@router.get(
    "/siswa", response_model=SiswaDashboard, dependencies=[Depends(require_roles(Role.siswa))]
)
def siswa_dashboard(current_user: CurrentUser, session: SessionDep) -> SiswaDashboard:
    return build_siswa_dashboard(session, current_user)


@router.get(
    "/tenant", response_model=TenantOverview, dependencies=[Depends(require_roles(Role.admin))]
)
def tenant_overview(current_user: CurrentUser, session: SessionDep) -> TenantOverview:
    """Own-institution overview (quota usage, period, active classes) for the admin."""
    tenant = session.get(Tenant, current_user.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant tidak ditemukan")
    return build_tenant_overviews(session, [tenant])[0]


@router.get(
    "/platform",
    response_model=PlatformDashboard,
    dependencies=[Depends(require_roles(Role.superadmin))],
)
def platform_dashboard(session: SessionDep) -> PlatformDashboard:
    return build_platform_dashboard(session)
