"""Class & enrollment request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.user import UserRead


class ClassCreate(BaseModel):
    name: str
    academic_year: str
    guru_id: int
    tenant_id: int | None = None


class ClassUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    academic_year: str | None = Field(default=None, min_length=1, max_length=9)
    guru_id: int | None = None


class ClassArchiveUpdate(BaseModel):
    archived: bool


class ClassRead(BaseModel):
    id: int
    tenant_id: int
    name: str
    academic_year: str
    archived: bool = False
    guru_id: int
    guru: UserRead | None = None
    student_count: int = 0

    model_config = {"from_attributes": True}


class ClassDetail(ClassRead):
    students: list[UserRead] = []


class EnrollmentCreate(BaseModel):
    siswa_id: int


class BulkEnrollmentCreate(BaseModel):
    siswa_ids: list[int] = Field(min_length=1, max_length=200)
