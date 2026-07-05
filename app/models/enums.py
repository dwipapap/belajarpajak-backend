"""Shared enumerations used across models and schemas."""

from __future__ import annotations

from enum import StrEnum


class TenantType(StrEnum):
    smk = "smk"
    kampus = "kampus"
    lembaga = "lembaga"


class Role(StrEnum):
    superadmin = "superadmin"
    admin = "admin"
    guru = "guru"
    siswa = "siswa"
