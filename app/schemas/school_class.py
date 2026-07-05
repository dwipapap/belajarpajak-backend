"""Class & enrollment request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.user import UserRead


class ClassCreate(BaseModel):
    name: str
    academic_year: str
    guru_id: int


class ClassRead(BaseModel):
    id: int
    tenant_id: int
    name: str
    academic_year: str
    guru_id: int

    model_config = {"from_attributes": True}


class ClassDetail(ClassRead):
    guru: UserRead | None = None
    students: list[UserRead] = []


class EnrollmentCreate(BaseModel):
    siswa_id: int
