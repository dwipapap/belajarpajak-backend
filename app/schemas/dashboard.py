"""Dashboard summary schema — a flexible, role-shaped counts payload."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class DashboardSummary(BaseModel):
    """Role-shaped counts. Only the keys relevant to the caller's role are populated."""

    tenants: int | None = None
    users: int | None = None
    classes: int | None = None
    guru: int | None = None
    siswa: int | None = None


# --- Guru dashboard -----------------------------------------------------------


class GuruClassOption(BaseModel):
    id: int
    name: str
    academic_year: str
    archived: bool
    student_count: int
    pending_grading: int


class GuruStudentProgress(BaseModel):
    id: int
    full_name: str
    identifier: str | None
    ebupot_issued: int
    ebupot_draft: int
    faktur_keluaran_issued: int
    faktur_masukan_credited: int
    spt_reported: int
    spt_ppn: int
    spt_pph_unifikasi: int
    spt_pph_21_26: int
    billing_paid: int
    pending_grading: int
    last_activity_at: datetime | None
    has_activity: bool


class GradingItem(BaseModel):
    """One document waiting for a teacher score."""

    module: str
    module_label: str
    id: int
    title: str
    period: str | None
    siswa_id: int
    siswa_name: str
    at: datetime | None


class GradingModuleCount(BaseModel):
    module: str
    module_label: str
    count: int


class GuruDashboard(BaseModel):
    classes: list[GuruClassOption]
    class_id: int | None
    students: list[GuruStudentProgress]
    grading_total: int
    grading_by_module: list[GradingModuleCount]
    grading_items: list[GradingItem]


# --- Siswa dashboard ----------------------------------------------------------


class StatusCounts(BaseModel):
    draft: int = 0
    issued: int = 0
    invalid: int = 0
    total: int = 0


class SiswaSptCounts(BaseModel):
    draft: int = 0
    awaiting_payment: int = 0
    reported: int = 0
    #: draft + awaiting_payment: not yet reported.
    unreported: int = 0
    total: int = 0


class SiswaMasukanCounts(BaseModel):
    not_credited: int = 0
    credited: int = 0
    uncredited: int = 0
    total: int = 0


class SiswaBillingCounts(BaseModel):
    unpaid: int = 0
    unpaid_amount: int = 0
    paid: int = 0
    tagihan_open: int = 0


class SiswaGradeStats(BaseModel):
    reviewed: int = 0
    average: float | None = None


class SiswaDocument(BaseModel):
    module: str
    module_label: str
    id: int
    title: str
    period: str | None
    status: str
    amount: int | None
    score: int | None
    teacher_feedback: str | None
    at: datetime | None


class SiswaTodo(BaseModel):
    kind: str  # draft | spt | billing | masukan
    module: str
    id: int | None
    title: str
    detail: str | None
    module_label: str
    at: datetime | None


class SiswaDashboard(BaseModel):
    classes: int
    ebupot: StatusCounts
    ebupot_by_type: dict[str, StatusCounts]
    faktur_keluaran: StatusCounts
    faktur_masukan: SiswaMasukanCounts
    spt: SiswaSptCounts
    billing: SiswaBillingCounts
    grades: SiswaGradeStats
    todos: list[SiswaTodo]
    todo_total: int
    recent: list[SiswaDocument]
    graded: list[SiswaDocument]


# --- Tenant overview (admin) and platform (superadmin) --------------------------


class QuotaUsage(BaseModel):
    used: int
    limit: int | None
    full: bool


class TenantOverview(BaseModel):
    tenant_id: int
    name: str
    slug: str
    type: str
    is_active: bool
    berlaku_dari: date | None
    berlaku_sampai: date | None
    days_remaining: int | None
    expired: bool
    expired_soon: bool
    guru: QuotaUsage
    siswa: QuotaUsage
    kelas: QuotaUsage
    active_classes: int


class PlatformDashboard(BaseModel):
    tenants_total: int
    tenants_active: int
    tenants_expiring: int
    users: int
    classes: int
    tenants: list[TenantOverview]
