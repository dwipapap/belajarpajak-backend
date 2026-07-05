"""FastAPI dependencies: DB session, current user, role guards, tenant scoping."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session
from sqlmodel.sql.expression import SelectOfScalar

from app.core.security import TOKEN_TYPE_ACCESS, decode_token
from app.db import get_session
from app.models.enums import Role
from app.models.user import User

# auto_error=False so we can raise a consistent 401 shape ourselves.
_bearer = HTTPBearer(auto_error=False)

SessionDep = Annotated[Session, Depends(get_session)]

_CREDENTIALS_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Kredensial tidak valid atau sesi berakhir",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    """Decode the access JWT and load the corresponding active user."""
    if credentials is None or not credentials.credentials:
        raise _CREDENTIALS_EXC
    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise _CREDENTIALS_EXC from exc

    if payload.get("type") != TOKEN_TYPE_ACCESS:
        raise _CREDENTIALS_EXC

    user_id = payload.get("sub")
    if user_id is None:
        raise _CREDENTIALS_EXC

    user = session.get(User, int(user_id))
    if user is None or not user.is_active:
        raise _CREDENTIALS_EXC
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: Role) -> Callable[[User], User]:
    """Dependency factory that allows only the given roles."""

    allowed = set(roles)

    def _guard(current_user: CurrentUser) -> User:
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Anda tidak memiliki akses ke sumber daya ini",
            )
        return current_user

    return _guard


def tenant_filter[T](query: SelectOfScalar[T], user: User, model: type) -> SelectOfScalar[T]:
    """Scope a query to the user's tenant.

    Superadmin (tenant_id is None) sees everything. Every other role is confined to
    their own tenant. ``model`` must expose a ``tenant_id`` column. Centralising this
    here means tenant isolation is one obvious call at every call site — never rely on
    the frontend to filter.
    """
    if user.role == Role.superadmin:
        return query
    return query.where(model.tenant_id == user.tenant_id)
