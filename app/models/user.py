"""User model. Single role per user (enum). tenant_id is NULL only for superadmin."""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Index
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field

from app.models.base import TimestampMixin
from app.models.enums import Role


class User(TimestampMixin, table=True):
    __tablename__ = "users"
    __table_args__ = (
        # Email is globally unique (login identifier for admin/guru/superadmin).
        Index("uq_users_email", "email", unique=True),
        # Siswa login identifier (NIK/NISN) — globally unique, NULL for the
        # email-login roles. Postgres allows many NULLs in a unique index.
        Index("uq_users_username", "username", unique=True),
    )

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: int | None = Field(
        default=None,
        sa_column=Column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True),
    )
    email: str = Field(max_length=255, index=True)
    #: Siswa-only login identifier (NIK/NISN). Email stays a generated
    #: placeholder for bulk-created siswa so every account keeps one.
    username: str | None = Field(default=None, max_length=32, index=True)
    password_hash: str = Field(max_length=255)
    #: True for freshly created (bulk) accounts — the UI forces a password
    #: change before any other request succeeds.
    must_change_password: bool = Field(default=False, nullable=False)
    full_name: str = Field(max_length=150)
    role: Role = Field(sa_column=Column(SAEnum(Role, name="user_role"), nullable=False))
    is_active: bool = Field(default=True, nullable=False)
