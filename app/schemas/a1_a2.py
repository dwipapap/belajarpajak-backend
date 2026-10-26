"""Schemas for the 1721-A1 / 1721-A2 annual aggregate views.

Both forms are read-only December aggregates of the year's monthly (BPMP)
slips: annual gross, annual withholding, identity, and the monthly rows the
detail view computes from. There is deliberately no create schema — no
endpoint creates A1/A2 rows.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class A1A2MonthlyRow(BaseModel):
    tax_month: int
    gross_income: int
    income_tax: int
    withholding_number: str | None


class A1A2Summary(BaseModel):
    form: str = Field(description="A1 (private payer) or A2 (state payer)")
    recipient_identity_number: str
    recipient_name: str
    tax_year: int
    annual_gross: int
    annual_tax: int
    months_covered: list[int]
    slips_count: int
    ter_category: str | None


class A1A2Detail(A1A2Summary):
    rows: list[A1A2MonthlyRow]


class A1A2ListResponse(BaseModel):
    items: list[A1A2Summary]
    total: int
