"""Authentication routes: login, refresh, me."""

from __future__ import annotations

import jwt
from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from app.core.deps import CurrentUser, SessionDep
from app.core.security import (
    TOKEN_TYPE_REFRESH,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.auth import (
    AccessTokenResponse,
    LoginRequest,
    MeResponse,
    RefreshRequest,
    TokenPair,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Email atau kata sandi salah",
)


def _resolve_login_user(session: SessionDep, data: LoginRequest) -> User:
    """Resolve which user a login attempt refers to.

    - tenant_slug given: match within that tenant.
    - omitted: try superadmin first, else the email in exactly one tenant.
    - ambiguous (email in multiple tenants): 409 asking for tenant slug.
    """
    email = data.email.lower()

    if data.tenant_slug:
        tenant = session.exec(
            select(Tenant).where(Tenant.slug == data.tenant_slug)
        ).first()
        if tenant is None:
            raise _INVALID_CREDENTIALS
        user = session.exec(
            select(User).where(User.tenant_id == tenant.id, User.email == email)
        ).first()
        if user is None:
            raise _INVALID_CREDENTIALS
        return user

    # No tenant slug: superadmin takes precedence.
    superadmin = session.exec(
        select(User).where(User.email == email, User.tenant_id.is_(None))
    ).first()
    if superadmin is not None:
        return superadmin

    matches = session.exec(
        select(User).where(User.email == email, User.tenant_id.is_not(None))
    ).all()
    if len(matches) == 0:
        raise _INVALID_CREDENTIALS
    if len(matches) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email terdaftar di beberapa institusi. Sertakan Kode Sekolah/Lembaga.",
        )
    return matches[0]


@router.post("/login", response_model=TokenPair)
def login(data: LoginRequest, session: SessionDep) -> TokenPair:
    user = _resolve_login_user(session, data)
    if not user.is_active or not verify_password(data.password, user.password_hash):
        raise _INVALID_CREDENTIALS
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
    return AccessTokenResponse(
        access_token=create_access_token(
            user_id=user.id, role=user.role.value, tenant_id=user.tenant_id
        )
    )


@router.get("/me", response_model=MeResponse)
def me(current_user: CurrentUser, session: SessionDep) -> MeResponse:
    tenant_name: str | None = None
    if current_user.tenant_id is not None:
        tenant = session.get(Tenant, current_user.tenant_id)
        tenant_name = tenant.name if tenant else None
    return MeResponse(
        id=current_user.id,
        email=current_user.email,
        full_name=current_user.full_name,
        role=current_user.role,
        tenant_id=current_user.tenant_id,
        tenant_name=tenant_name,
        is_active=current_user.is_active,
    )
