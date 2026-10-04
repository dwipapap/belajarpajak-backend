"""Audit log — who did what, per institution.

Written by ``app.services.audit.log_action`` at the sensitive moments: account
lifecycle, password resets, class changes, tenant lifecycle, document
lifecycle, SPT submission and billing payment. Admin reads their own
institution's trail; the superadmin reads everything.
"""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey
from sqlmodel import Field

from app.models.base import TimestampMixin


class AuditLog(TimestampMixin, table=True):
    __tablename__ = "audit_logs"

    id: int | None = Field(default=None, primary_key=True)
    #: NULL only for superadmin-global actions (e.g. creating a tenant).
    tenant_id: int | None = Field(
        default=None,
        sa_column=Column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True),
    )
    #: Kept even if the actor account is later deleted (SET NULL on the FK,
    #: identity preserved in actor_email).
    actor_id: int | None = Field(
        default=None,
        sa_column=Column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    actor_email: str = Field(max_length=255)
    actor_role: str = Field(max_length=20)

    #: Dotted action code, e.g. ``user.password_reset``, ``document.issue``.
    action: str = Field(max_length=60, index=True)
    entity: str = Field(max_length=40, index=True)
    entity_id: int | None = Field(default=None)
    summary: str = Field(default="", max_length=500)
