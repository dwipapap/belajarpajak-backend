"""Authentication routes: login, refresh, me, change-password."""

from __future__ import annotations

from datetime import date

import jwt
from fastapi import APIRouter, HTTPException, Request, status
from sqlmodel import select

from app.core.config import settings
from app.core.deps import CurrentUser, SessionDep
from app.core.rate_limit import login_limiter
from app.core.security import (
    TOKEN_TYPE_REFRESH,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.auth import (
    AccessTokenResponse,
    ChangePasswordRequest,
    LoginRequest,
    MeResponse,
    RefreshRequest,
    TokenPair,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Email/username atau kata sandi salah",
)

_TENANT_INACTIVE = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Institusi ini sedang tidak aktif. Hubungi pemilik sistem.",
)

_TENANT_EXPIRED = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Masa berlaku instansi telah habis, hubungi superadmin",
)


def _tenant_expired(tenant: Tenant) -> bool:
    return (
        tenant.berlaku_sampai is not None
        and tenant.berlaku_sampai < date.today()
    )


def _resolve_login_user(session: SessionDep, data: LoginRequest) -> User:
    """Resolve the login handle.

    Email and username are both globally unique, so one identifier is enough —
    no school code. Emails are matched case-insensitively; usernames exactly.
    """
    identifier = data.identifier.strip()
    by_email = session.exec(
        select(User).where(User.email == identifier.lower())
    ).first()
    user = by_email
    if user is None:
        user = session.exec(select(User).where(User.username == identifier)).first()
    if user is None:
        raise _INVALID_CREDENTIALS
    return user


def _ensure_login_allowed(user: User, session: SessionDep) -> None:
    if not user.is_active:
        raise _INVALID_CREDENTIALS
    if user.tenant_id is not None:
        tenant = session.get(Tenant, user.tenant_id)
        if tenant is None or not tenant.is_active:
            raise _TENANT_INACTIVE
        if _tenant_expired(tenant):
            raise _TENANT_EXPIRED


def _ensure_not_rate_limited(identifier: str, ip: str | None) -> None:
    if not settings.LOGIN_RATE_LIMIT_ENABLED:
        return
    wait = login_limiter.retry_after(identifier, ip)
    if wait is not None:
        minutes = max(1, -(-wait // 60))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Terlalu banyak percobaan masuk yang gagal. Coba lagi dalam {minutes} menit.",
            headers={"Retry-After": str(wait)},
        )


@router.post("/login", response_model=TokenPair)
def login(data: LoginRequest, session: SessionDep, request: Request) -> TokenPair:
    identifier = data.identifier.strip()
    ip = request.client.host if request.client else None
    _ensure_not_rate_limited(identifier, ip)
    try:
        user = _resolve_login_user(session, data)
        if not verify_password(data.password, user.password_hash):
            raise _INVALID_CREDENTIALS
    except HTTPException:
        if settings.LOGIN_RATE_LIMIT_ENABLED:
            login_limiter.record_failure(identifier, ip)
        raise
    _ensure_login_allowed(user, session)
    login_limiter.reset_identifier(identifier)
    return TokenPair(
        access_token=create_access_token(
            user_id=user.id, role=user.role.value, tenant_id=user.tenant_id
        ),
        refresh_token=create_refresh_token(user_id=user.id),
    )


@router.post("/refresh", response_model=AccessTokenResponse)
def refresh(data: RefreshRequest, session: SessionDep) -> AccessTokenResponse:
    try:
        payload = decode_token(data.refresh_token)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token tidak valid"
        ) from exc
    if payload.get("type") != TOKEN_TYPE_REFRESH:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token tidak valid"
        )
    user = session.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Pengguna tidak aktif"
        )
    _ensure_login_allowed(user, session)
    return AccessTokenResponse(
        access_token=create_access_token(
            user_id=user.id, role=user.role.value, tenant_id=user.tenant_id
        )
    )


def _build_me_response(user: User, session: SessionDep) -> MeResponse:
    from app.core.modules import (
        ALL_MODULE_KEYS,
        DEFAULT_SETTINGS,
        resolve_effective_modules,
        resolve_tenant_settings,
    )
    from app.schemas.auth import LearningSettingsRead

    tenant_name: str | None = None
    enabled_modules: list[str] = list(ALL_MODULE_KEYS)
    settings_dict: dict[str, bool] = dict(DEFAULT_SETTINGS)

    if user.tenant_id is not None:
        tenant = session.get(Tenant, user.tenant_id)
        if tenant is not None:
            tenant_name = tenant.name
            enabled_modules = resolve_effective_modules(tenant)
            settings_dict = resolve_tenant_settings(tenant)

    return MeResponse(
        id=user.id,
        email=user.email,
        username=user.username,
        full_name=user.full_name,
        role=user.role,
        tenant_id=user.tenant_id,
        tenant_name=tenant_name,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        enabled_modules=enabled_modules,
        settings=LearningSettingsRead(**settings_dict),
    )


@router.get("/me", response_model=MeResponse)
def me(current_user: CurrentUser, session: SessionDep) -> MeResponse:
    return _build_me_response(current_user, session)


@router.post("/change-password", response_model=MeResponse)
def change_password(
    data: ChangePasswordRequest,
    current_user: CurrentUser,
    session: SessionDep,
) -> MeResponse:
    """Set a new password with the current one — clears must_change_password.

    This is the gate every freshly created (bulk) account must pass through
    before doing anything else; it also serves the voluntary password change.
    """
    if not verify_password(data.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Kata sandi saat ini salah",
        )
    current_user.password_hash = hash_password(data.new_password)
    current_user.must_change_password = False
    session.add(current_user)
    session.commit()
    session.refresh(current_user)
    return _build_me_response(current_user, session)

