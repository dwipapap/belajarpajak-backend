"""Audit trail routes — admin reads its institution, superadmin reads all."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import Session

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.models.audit_log import AuditLog
from app.models.enums import Role
from app.services import audit as audit_service

router = APIRouter(prefix="/audit-logs", tags=["audit-logs"])

_admin_or_super = Depends(require_roles(Role.superadmin, Role.admin))


class AuditLogRead(BaseModel):
    id: int
    tenant_id: int | None
    actor_id: int | None
    actor_email: str
    actor_role: str
    action: str
    entity: str
    entity_id: int | None
    summary: str
    created_at: datetime | None


class AuditLogListResponse(BaseModel):
    items: list[AuditLogRead]
    total: int
    page: int
    size: int


@router.get("", response_model=AuditLogListResponse, dependencies=[_admin_or_super])
def list_audit_logs(
    current_user: CurrentUser,
    session: SessionDep,
    tenant_id: int | None = None,
    action: str | None = None,
    entity: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> AuditLogListResponse:
    """Admin is pinned to its own institution; superadmin may filter any
    tenant (omit ``tenant_id`` for the global trail)."""
    effective_tenant = current_user.tenant_id if current_user.role == Role.admin else tenant_id
    logs, total = audit_service.list_logs(
        session,
        tenant_id=effective_tenant,
        action=action,
        entity=entity,
        page=page,
        size=size,
    )
    return AuditLogListResponse(
        items=[
            AuditLogRead(
                id=log.id,
                tenant_id=log.tenant_id,
                actor_id=log.actor_id,
                actor_email=log.actor_email,
                actor_role=log.actor_role,
                action=log.action,
                entity=log.entity,
                entity_id=log.entity_id,
                summary=log.summary,
                created_at=log.created_at,
            )
            for log in logs
        ],
        total=total,
        page=page,
        size=size,
    )


# Keep the Session import meaningful for type checkers reading the model dump
# helper above; AuditLog rows are converted explicitly, not via from_attributes.
_ = (Session, AuditLog)
