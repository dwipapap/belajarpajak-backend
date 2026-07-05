"""SchoolClass model (table `classes` — `class` is a reserved word in Python)."""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey
from sqlmodel import Field

from app.models.base import TimestampMixin


class SchoolClass(TimestampMixin, table=True):
    __tablename__ = "classes"

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: int = Field(
        sa_column=Column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    name: str = Field(max_length=100)
    academic_year: str = Field(max_length=9)  # e.g. "2026/2027"
    guru_id: int = Field(
        sa_column=Column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    )
