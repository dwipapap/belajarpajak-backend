"""Dashboard summary schema — a flexible, role-shaped counts payload."""

from __future__ import annotations

from pydantic import BaseModel


class DashboardSummary(BaseModel):
    """Role-shaped counts. Only the keys relevant to the caller's role are populated."""

    tenants: int | None = None
    users: int | None = None
    classes: int | None = None
    guru: int | None = None
    siswa: int | None = None
