"""Audit logging helper — one obvious call at every sensitive moment."""

from __future__ import annotations

from sqlmodel import Session, select

from app.models.audit_log import AuditLog
from app.models.user import User


def log_action(
    session: Session,
    *,
    actor: User,
    action: str,
    entity: str,
    entity_id: int | None = None,
    tenant_id: int | None = None,
    summary: str = "",
) -> None:
    """Record one audit entry for ``actor``; commit left to the caller.

    ``tenant_id`` defaults to the actor's tenant (NULL for the superadmin, who
    must pass an explicit tenant when the action belongs to one institution).
    """
    session.add(
        AuditLog(
            tenant_id=tenant_id if tenant_id is not None else actor.tenant_id,
            actor_id=actor.id,
            actor_email=actor.email,
            actor_role=actor.role.value,
            action=action,
            entity=entity,
            entity_id=entity_id,
            summary=summary[:500],
        )
    )


def list_logs(
    session: Session,
    *,
    tenant_id: int | None = None,
    action: str | None = None,
    entity: str | None = None,
    page: int = 1,
    size: int = 50,
) -> tuple[list[AuditLog], int]:
    """Newest-first audit trail, scoped by the caller's role at the router."""
    conditions = []
    if tenant_id is not None:
        conditions.append(AuditLog.tenant_id == tenant_id)
    if action is not None:
        conditions.append(AuditLog.action == action)
    if entity is not None:
        conditions.append(AuditLog.entity == entity)

    base = select(AuditLog).where(*conditions)
    from sqlmodel import func

    total = session.exec(
        select(func.count()).select_from(AuditLog).where(*conditions)
    ).one()
    items = session.exec(
        base.order_by(AuditLog.id.desc()).offset((page - 1) * size).limit(size)
    ).all()
    return list(items), total
