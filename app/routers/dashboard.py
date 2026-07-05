"""Dashboard summary route — role-shaped counts."""

from __future__ import annotations

from fastapi import APIRouter
from sqlmodel import func, select

from app.core.deps import CurrentUser, SessionDep
from app.models.enrollment import Enrollment
from app.models.enums import Role
from app.models.school_class import SchoolClass
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.dashboard import DashboardSummary

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
                select(func.count()).select_from(User).where(
                    User.tenant_id == tid, User.role == Role.guru
                ),
            ),
            siswa=_count(
                session,
                select(func.count()).select_from(User).where(
                    User.tenant_id == tid, User.role == Role.siswa
                ),
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
