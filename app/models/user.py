"""User model. Single role per user (enum). tenant_id is NULL only for superadmin."""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Index, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field

from app.models.base import TimestampMixin
from app.models.enums import Role


class User(TimestampMixin, table=True):
    __tablename__ = "users"
    __table_args__ = (
        # Email is unique within a tenant.
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        # Superadmin (tenant_id IS NULL) email is globally unique — enforced via a
        # partial index because SQL treats NULLs in a composite unique as distinct.
        Index(
            "uq_users_superadmin_email",
            "email",
            unique=True,
            postgresql_where=Column("tenant_id").is_(None),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: int | None = Field(
        default=None,
        sa_column=Column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True),
    )
    email: str = Field(max_length=255, index=True)
    password_hash: str = Field(max_length=255)
    full_name: str = Field(max_length=150)
    role: Role = Field(sa_column=Column(SAEnum(Role, name="user_role"), nullable=False))
    is_active: bool = Field(default=True, nullable=False)
