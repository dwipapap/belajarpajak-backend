"""Enrollment model — links a siswa (student) to a class."""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, UniqueConstraint
from sqlmodel import Field

from app.models.base import TimestampMixin


class Enrollment(TimestampMixin, table=True):
    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint("class_id", "siswa_id", name="uq_enrollments_class_siswa"),
    )

    id: int | None = Field(default=None, primary_key=True)
    class_id: int = Field(
        sa_column=Column(ForeignKey("classes.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    siswa_id: int = Field(
        sa_column=Column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    )
