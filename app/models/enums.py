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


class SlipType(StrEnum):
    """e-Bupot form type. All types share one table, lifecycle, and feature set."""

    bp21 = "bp21"
    bp26 = "bp26"


class SlipStatus(StrEnum):
    draft = "draft"
    issued = "issued"
    invalid = "invalid"


class SlipTaxNature(StrEnum):
    final = "final"
    non_final = "non_final"


class SlipTaxFacility(StrEnum):
    none = "none"
    dtp = "dtp"
    skb = "skb"
    rate_0 = "rate_0"


class SlipSptFlag(StrEnum):
    """Issued-only lifecycle flags for the 'Telah Terbit' filters."""

    reported_in_spt = "reported_in_spt"
    objection_in_progress = "objection_in_progress"
    objection_completed = "objection_completed"
    objection_rejected_formal = "objection_rejected_formal"
    objection_withdrawal_review = "objection_withdrawal_review"
    objection_withdrawal_accepted = "objection_withdrawal_accepted"
    spt_audited = "spt_audited"
    spt_legal_process = "spt_legal_process"
