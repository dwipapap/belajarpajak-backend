"""User management routes (superadmin + admin, tenant-scoped).

Roles: admin creates only guru/siswa inside its institution — the same power
the superadmin has on any institution. Documents/grades are out of scope here.
Bulk creation provisions many accounts at once with the bootstrap password and
``must_change_password`` so every recipient sets a personal password at login.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlmodel import func, select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.core.rate_limit import login_limiter
from app.core.security import DEFAULT_PASSWORD, hash_password
from app.models.enrollment import Enrollment
from app.models.enums import Role
from app.models.school_class import SchoolClass
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.user import (
    UserBulkCreate,
    UserBulkResult,
    UserBulkResultRow,
    UserCreate,
    UserListResponse,
    UserRead,
    UserUpdate,
)
from app.services.audit import log_action

router = APIRouter(prefix="/users", tags=["users"])

_admin_or_super = Depends(require_roles(Role.superadmin, Role.admin))


def _resolve_target_tenant(
    session: SessionDep, current_user: User, requested_tenant_id: int | None
) -> int:
    """Admin is always confined to own tenant; superadmin may target any tenant."""
    if current_user.role == Role.admin:
        return current_user.tenant_id
    if requested_tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="tenant_id wajib untuk peran non-superadmin",
        )
    tenant = session.get(Tenant, requested_tenant_id)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="tenant_id tidak valid"
        )
    return requested_tenant_id


def _enforce_quota(session: SessionDep, tenant_id: int, role: Role, adding: int = 1) -> None:
    """Reject creation beyond the institution's quota (NULL = unlimited)."""
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        return
    limit = {
        Role.guru: tenant.max_guru,
        Role.siswa: tenant.max_siswa,
    }.get(role)
    if limit is None:
        return
    current = session.exec(
        select(func.count())
        .select_from(User)
        .where(User.tenant_id == tenant_id, User.role == role)
    ).one()
    if current + adding > limit:
        label = "guru" if role == Role.guru else "siswa"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Kuota {label} instansi penuh (maksimal {limit})",
        )


def _ensure_email_free(session: SessionDep, email: str, exclude_id: int | None = None) -> None:
    query = select(User).where(User.email == email.lower())
    existing = session.exec(query).first()
    if existing is not None and existing.id != exclude_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email sudah terdaftar di sistem",
        )


def _ensure_username_free(
    session: SessionDep, username: str, exclude_id: int | None = None
) -> None:
    existing = session.exec(select(User).where(User.username == username)).first()
    if existing is not None and existing.id != exclude_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username/NIK/NISN sudah terdaftar di sistem",
        )


def _create_account(
    session: SessionDep,
    *,
    tenant_id: int,
    role: Role,
    email: str,
    full_name: str,
    password: str,
    username: str | None = None,
    must_change_password: bool = False,
) -> User:
    _ensure_email_free(session, email)
    if username is not None:
        _ensure_username_free(session, username)
    user = User(
        tenant_id=tenant_id,
        email=email.lower(),
        username=username,
        password_hash=hash_password(password),
        must_change_password=must_change_password,
        full_name=full_name,
        role=role,
    )
    session.add(user)
    session.flush()
    return user


@router.get("", response_model=UserListResponse, dependencies=[_admin_or_super])
def list_users(
    current_user: CurrentUser,
    session: SessionDep,
    role: Role | None = None,
    tenant_id: int | None = None,
    q: str | None = None,
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
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        conditions.append(
            or_(
                User.full_name.ilike(pattern),
                User.email.ilike(pattern),
                User.username.ilike(pattern),
            )
        )

    total = session.exec(select(func.count()).select_from(User).where(*conditions)).one()
    items = session.exec(
        select(User)
        .where(*conditions)
        .order_by(User.id)
        .offset((page - 1) * size)
        .limit(size)
    ).all()

    user_ids = [u.id for u in items if u.id is not None]
    class_map: dict[int, list[str]] = {uid: [] for uid in user_ids}
    if user_ids:
        rows = session.exec(
            select(Enrollment.siswa_id, SchoolClass.name)
            .join(SchoolClass, SchoolClass.id == Enrollment.class_id)
            .where(
                Enrollment.siswa_id.in_(user_ids),
                SchoolClass.archived.is_(False),
            )
            .order_by(SchoolClass.name)
        ).all()
        for sid, cname in rows:
            class_map.setdefault(sid, []).append(cname)

    user_reads = [
        UserRead.model_validate(u).model_copy(update={"class_names": class_map.get(u.id, [])})
        for u in items
    ]
    return UserListResponse(
        items=user_reads,
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
    if current_user.role == Role.admin:
        if data.role not in (Role.guru, Role.siswa):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Hanya akun guru atau siswa yang dapat dibuat di instansi",
            )
    elif data.role not in (Role.admin, Role.guru, Role.siswa):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Hanya akun admin, guru, atau siswa yang dapat dibuat di instansi",
        )

    tenant_id = _resolve_target_tenant(session, current_user, data.tenant_id)
    _enforce_quota(session, tenant_id, data.role)

    if data.role == Role.siswa:
        if not data.username:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Siswa wajib memiliki username (NIK/NISN)",
            )
        email = data.email or f"{data.username}@siswa.pajaksim.local"
        username = data.username
    else:
        if not data.email:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Email wajib diisi untuk peran selain siswa",
            )
        email = data.email
        username = None

    user = _create_account(
        session,
        tenant_id=tenant_id,
        role=data.role,
        email=email,
        full_name=data.full_name,
        password=data.password,
        username=username,
    )
    log_action(
        session,
        actor=current_user,
        action="user.create",
        entity="user",
        entity_id=user.id,
        tenant_id=tenant_id,
        summary=f"Membuat akun {data.role.value} {data.full_name} ({user.email})",
    )
    session.commit()
    session.refresh(user)
    return user


@router.post(
    "/bulk",
    response_model=UserBulkResult,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_admin_or_super],
)
def bulk_create_users(
    data: UserBulkCreate, current_user: CurrentUser, session: SessionDep
) -> UserBulkResult:
    """Provision many guru/siswa accounts at once.

    Each row is validated independently — duplicates report as failed rows
    while the rest still get created (the same partial-success semantics as
    the document bulk endpoints). Accounts without an explicit password start
    with the shared bootstrap password and must change it at login.
    """
    if data.role not in (Role.guru, Role.siswa):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Hanya akun guru atau siswa yang dapat dibuat di instansi",
        )

    tenant_id = _resolve_target_tenant(session, current_user, data.tenant_id)

    target_class: SchoolClass | None = None
    if data.class_id is not None and data.role == Role.siswa:
        target_class = session.get(SchoolClass, data.class_id)
        if target_class is None or target_class.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="class_id tidak valid untuk instansi ini",
            )

    _enforce_quota(session, tenant_id, data.role, adding=len(data.accounts))

    results: list[UserBulkResultRow] = []
    created = 0
    for index, item in enumerate(data.accounts):
        try:
            email = item.email
            username = item.username if data.role == Role.siswa else None
            if data.role == Role.siswa and not username:
                raise HTTPException(status_code=422, detail="NISN/NIK wajib diisi")
            if data.role == Role.guru and not email:
                raise HTTPException(status_code=422, detail="Email guru wajib diisi")
            if email is None:
                # Placeholder keeps the email-unique invariant; siswa logs in
                # with the username, never the email.
                email = f"{username}@siswa.pajaksim.local"

            pw = item.password or data.default_password or DEFAULT_PASSWORD
            must_change = (
                data.must_change_password
                if data.must_change_password is not None
                else (item.password is None)
            )
            user = _create_account(
                session,
                tenant_id=tenant_id,
                role=data.role,
                email=email,
                full_name=item.full_name,
                password=pw,
                username=username,
                must_change_password=must_change,
            )
            if target_class is not None:
                session.add(Enrollment(class_id=target_class.id, siswa_id=user.id))
            created += 1
            results.append(
                UserBulkResultRow(
                    index=index, ok=True, detail="ok", user_id=user.id,
                    email=user.email, username=user.username,
                )
            )
        except HTTPException as exc:
            results.append(
                UserBulkResultRow(
                    index=index, ok=False,
                    detail=exc.detail if isinstance(exc.detail, str) else "Gagal membuat akun",
                    email=item.email, username=item.username,
                )
            )

    summary = f"Bulk create {created} akun {data.role.value} ({len(results) - created} gagal)"
    if target_class is not None:
        summary += f" ke kelas {target_class.name}"
    log_action(
        session,
        actor=current_user,
        action="user.bulk_create",
        entity="user",
        entity_id=None,
        tenant_id=tenant_id,
        summary=summary,
    )
    session.commit()
    return UserBulkResult(created=created, failed=len(results) - created, results=results)


@router.patch("/{user_id}", response_model=UserRead, dependencies=[_admin_or_super])
def update_user(
    user_id: int, data: UserUpdate, current_user: CurrentUser, session: SessionDep
) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pengguna tidak ditemukan"
        )
    # Admin may only touch guru/siswa inside its own tenant — never other
    # admins and never other institutions.
    is_foreign_or_staff = (
        user.tenant_id != current_user.tenant_id
        or (user.role != Role.guru and user.role != Role.siswa)
    )
    if current_user.role == Role.admin and is_foreign_or_staff:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Akses ditolak"
        )

    payload = data.model_dump(exclude_unset=True)
    if "email" in payload:
        raw_email = payload["email"]
        if not raw_email:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Email tidak boleh kosong",
            )
        normalized_email = raw_email.lower()
        if normalized_email != user.email:
            _ensure_email_free(session, normalized_email, exclude_id=user.id)
        payload["email"] = normalized_email

    if "username" in payload:
        if user.role != Role.siswa:
            payload.pop("username")
        else:
            new_username = payload["username"]
            if not new_username:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Siswa wajib memiliki username (NIK/NISN)",
                )
            if new_username != user.username:
                _ensure_username_free(session, new_username, exclude_id=user.id)

    resetting_password = payload.get("password") is not None
    if "password" in payload:
        raw_pw = payload.pop("password")
        if raw_pw is not None:
            user.password_hash = hash_password(raw_pw)
    deactivating = payload.get("is_active") is False

    old_email = user.email
    old_username = user.username
    for field, value in payload.items():
        setattr(user, field, value)
    session.add(user)

    if resetting_password:
        login_limiter.reset_identifier(old_email)
        login_limiter.reset_identifier(user.email)
        if old_username:
            login_limiter.reset_identifier(old_username)
        if user.username:
            login_limiter.reset_identifier(user.username)

    action = "user.password_reset" if resetting_password else (
        "user.deactivate" if deactivating else "user.update"
    )
    log_action(
        session,
        actor=current_user,
        action=action,
        entity="user",
        entity_id=user.id,
        tenant_id=user.tenant_id,
        summary=(
            f"Reset kata sandi akun {user.email}"
            if resetting_password
            else f"Perubahan akun {user.email} ({action.split('.')[1]})"
        ),
    )
    session.commit()
    session.refresh(user)
    return user
