"""User management routes (superadmin + admin, tenant-scoped)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import func, select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.core.security import hash_password
from app.models.enums import Role
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.user import UserCreate, UserListResponse, UserRead, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])

_admin_or_super = Depends(require_roles(Role.superadmin, Role.admin))


def _resolve_target_tenant(current_user: User, requested_tenant_id: int | None) -> int | None:
    """Admin is always confined to own tenant; superadmin may target any tenant."""
    if current_user.role == Role.admin:
        return current_user.tenant_id
    return requested_tenant_id  # superadmin


@router.get("", response_model=UserListResponse, dependencies=[_admin_or_super])
def list_users(
    current_user: CurrentUser,
    session: SessionDep,
    role: Role | None = None,
    tenant_id: int | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
) -> UserListResponse:
    conditions = []
    if current_user.role == Role.admin:
        # Admin is always confined to own tenant; the tenant_id param is ignored.
        conditions.append(User.tenant_id == current_user.tenant_id)
    elif tenant_id is not None:
        # Superadmin may narrow the list to one tenant (tenant switcher in the UI).
        conditions.append(User.tenant_id == tenant_id)
    if role is not None:
        conditions.append(User.role == role)

    total = session.exec(select(func.count()).select_from(User).where(*conditions)).one()
    items = session.exec(
        select(User)
        .where(*conditions)
        .order_by(User.id)
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    return UserListResponse(
        items=[UserRead.model_validate(u) for u in items],
        total=total,
        page=page,
        size=size,
    )


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_admin_or_super],
)
def create_user(data: UserCreate, current_user: CurrentUser, session: SessionDep) -> User:
    # Admins may only create guru/siswa within their own tenant.
    if current_user.role == Role.admin and data.role not in (Role.guru, Role.siswa):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin hanya dapat membuat akun guru atau siswa",
        )

    tenant_id = _resolve_target_tenant(current_user, data.tenant_id)

    # Non-superadmin roles must belong to a tenant; superadmin must not.
    if data.role == Role.superadmin:
        tenant_id = None
    elif tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="tenant_id wajib untuk peran non-superadmin",
        )
    elif session.get(Tenant, tenant_id) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="tenant_id tidak valid",
        )

    email = data.email.lower()
    dupe = session.exec(
        select(User).where(User.email == email, User.tenant_id == tenant_id)
    ).first()
    if dupe is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email sudah terdaftar pada institusi ini",
        )

    user = User(
        email=email,
        password_hash=hash_password(data.password),
        full_name=data.full_name,
        role=data.role,
        tenant_id=tenant_id,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserRead, dependencies=[_admin_or_super])
def update_user(
    user_id: int, data: UserUpdate, current_user: CurrentUser, session: SessionDep
) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pengguna tidak ditemukan"
        )
    # Admin may only touch users within their own tenant.
    if current_user.role == Role.admin and user.tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Akses lintas tenant ditolak"
        )

    payload = data.model_dump(exclude_unset=True)
    if "password" in payload:
        user.password_hash = hash_password(payload.pop("password"))
    for field, value in payload.items():
        setattr(user, field, value)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user
