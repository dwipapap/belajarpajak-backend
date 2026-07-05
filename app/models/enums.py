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


class Bp21Status(StrEnum):
    draft = "draft"
    issued = "issued"
    invalid = "invalid"


class Bp21TaxNature(StrEnum):
    final = "final"
    non_final = "non_final"


class Bp21TaxFacility(StrEnum):
    none = "none"
    dtp = "dtp"
    skb = "skb"
    rate_0 = "rate_0"
