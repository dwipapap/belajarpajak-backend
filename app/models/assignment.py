"""Tugas (class assignments) and student submission models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.models.base import TimestampMixin
from app.models.enums import AssignmentDocType, AssignmentStatus


class Assignment(TimestampMixin, table=True):
    """Tugas created by a guru for a specific class."""

    __tablename__ = "assignments"

    id: int | None = Field(default=None, primary_key=True)
    tenant_id: int = Field(
        sa_column=Column(
            ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    class_id: int = Field(
        sa_column=Column(
            ForeignKey("classes.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    created_by: int = Field(
        sa_column=Column(
            ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
        )
    )

    title: str = Field(max_length=200)
    instructions: str = Field(
        default="", sa_column=Column(Text, nullable=False, server_default="")
    )
    doc_type: AssignmentDocType = Field(
        sa_column=Column(String(length=40), nullable=False, index=True)
    )
    due_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True, index=True),
    )
    status: AssignmentStatus = Field(
        default=AssignmentStatus.draft,
        sa_column=Column(
            SAEnum(AssignmentStatus, name="assignment_status"),
            nullable=False,
            index=True,
        ),
    )


class AssignmentSubmission(TimestampMixin, table=True):
    """A student's submitted document for an Assignment, with auto-checks and teacher grade."""

    __tablename__ = "assignment_submissions"
    __table_args__ = (
        UniqueConstraint(
            "assignment_id", "siswa_id", name="uq_assignment_submission_siswa"
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    assignment_id: int = Field(
        sa_column=Column(
            ForeignKey("assignments.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    siswa_id: int = Field(
        sa_column=Column(
            ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    doc_id: int = Field(sa_column=Column(Integer, nullable=False, index=True))

    submitted_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False,
        )
    )
    is_late: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="false"),
    )
    auto_score: int = Field(
        default=0, ge=0, le=100, sa_column=Column(Integer, nullable=False)
    )
    auto_checks: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default="[]"),
    )
    final_score: int | None = Field(
        default=None, ge=0, le=100, sa_column=Column(Integer, nullable=True)
    )
    teacher_feedback: str | None = Field(default=None, max_length=500)
    graded_by: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
        ),
    )
    graded_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
