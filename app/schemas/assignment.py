"""Pydantic schemas for Tugas (assignments) and student submissions."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AssignmentDocType, AssignmentStatus


class AutoCheckRead(BaseModel):
    code: str
    label: str
    passed: bool
    message: str
    weight: int


class AssignmentCreate(BaseModel):
    class_id: int
    title: str = Field(min_length=1, max_length=200)
    instructions: str = Field(default="", max_length=20_000)
    doc_type: AssignmentDocType
    due_at: datetime | None = None
    publish: bool = False


class AssignmentUpdate(BaseModel):
    class_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    instructions: str | None = Field(default=None, max_length=20_000)
    doc_type: AssignmentDocType | None = None
    due_at: datetime | None = None


class SubmissionSubmitRequest(BaseModel):
    doc_id: int


class SubmissionGradeRequest(BaseModel):
    final_score: int = Field(ge=0, le=100)
    teacher_feedback: str | None = Field(default=None, max_length=500)


class CandidateDocRead(BaseModel):
    """A student's own document of matching doc_type available to submit."""

    id: int
    doc_type: AssignmentDocType
    number: str | None = None
    status: str
    tax_month: int
    tax_year: int
    counterparty_name: str | None = None
    dpp_amount: int = 0
    tax_amount: int = 0
    created_at: datetime


class SubmissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    assignment_id: int
    siswa_id: int
    siswa_name: str
    siswa_nis: str | None = None
    siswa_email: str
    submitted: bool = False
    doc_id: int | None = None
    doc_number: str | None = None
    doc_status: str | None = None
    doc_period: str | None = None
    doc_tax_amount: int | None = None
    submitted_at: datetime | None = None
    is_late: bool = False
    auto_score: int | None = None
    auto_checks: list[AutoCheckRead] = Field(default_factory=list)
    final_score: int | None = None
    displayed_score: int | None = None
    teacher_feedback: str | None = None
    graded_by: int | None = None
    graded_at: datetime | None = None


class AssignmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: int
    class_id: int
    class_name: str = "-"
    created_by: int
    creator_name: str = "-"
    title: str
    instructions: str
    doc_type: AssignmentDocType
    due_at: datetime | None = None
    status: AssignmentStatus
    created_at: datetime
    updated_at: datetime

    # Aggregate statistics for guru/admin views
    enrolled_count: int = 0
    submitted_count: int = 0
    graded_count: int = 0

    # Student-specific submission summary when queried by a siswa
    my_submission: SubmissionRead | None = None


class AssignmentDetailRead(AssignmentRead):
    """Full assignment view including all enrolled students' submission rows (for guru/admin)."""

    submissions: list[SubmissionRead] = Field(default_factory=list)
