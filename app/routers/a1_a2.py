"""Read-only 1721-A1 / 1721-A2 annual aggregate routes.

A1 covers permanent employees paid by private (non-state) payers; A2 covers
civil servants, TNI/POLRI members, and their pensioners paid via APBN/APBD.
Both are December aggregates of the year's issued monthly (BPMP) slips — the
payer-population split awaits a payroll source, so both views compute from
the same slip set and differ only in form label. No create/update/delete
endpoints: the detail view computes monthly rows from the slips themselves.

Scope pattern reuses the slip module: ``apply_access_filters`` gives the
same tenant/class/siswa visibility as the source documents.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.models.enums import Role, SlipStatus, SlipType
from app.models.slip import WithholdingSlip
from app.routers.slips import apply_access_filters
from app.schemas.a1_a2 import A1A2Detail, A1A2ListResponse, A1A2MonthlyRow, A1A2Summary
from app.services import ter as ter_service

_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru, Role.siswa))


def _summarize(form: str, tax_year: int, slips: list[WithholdingSlip]) -> A1A2Summary:
    ordered = sorted(slips, key=lambda slip: slip.tax_month)
    latest_ptkp = next(
        (slip.ptkp_status for slip in reversed(ordered) if slip.ptkp_status), None
    )
    return A1A2Summary(
        form=form,
        recipient_identity_number=ordered[0].recipient_identity_number,
        recipient_name=ordered[0].recipient_name,
        tax_year=tax_year,
        annual_gross=sum(slip.gross_income for slip in ordered),
        annual_tax=sum(slip.income_tax for slip in ordered),
        months_covered=sorted({slip.tax_month for slip in ordered}),
        slips_count=len(ordered),
        ter_category=ter_service.ter_category_for_ptkp(latest_ptkp),
    )


def make_a1_a2_router(form: str) -> APIRouter:
    """One read-only aggregate endpoint set per annual form (A1 or A2)."""
    router = APIRouter(prefix=f"/{form.lower()}", tags=[form.lower()])

    def _year_slips(
        current_user: CurrentUser, session: SessionDep, tax_year: int
    ) -> list[WithholdingSlip]:
        query = apply_access_filters(
            select(WithholdingSlip)
            .where(
                WithholdingSlip.slip_type == SlipType.bpmp,
                WithholdingSlip.status == SlipStatus.issued,
                WithholdingSlip.tax_year == tax_year,
            )
            .order_by(WithholdingSlip.recipient_identity_number, WithholdingSlip.tax_month),
            current_user,
        )
        return list(session.exec(query).all())

    @router.get("", response_model=A1A2ListResponse, dependencies=[_roles])
    def list_aggregates(
        current_user: CurrentUser,
        session: SessionDep,
        tax_year: Annotated[int, Query(ge=2020, le=2100)],
    ) -> A1A2ListResponse:
        grouped: dict[str, list[WithholdingSlip]] = {}
        for slip in _year_slips(current_user, session, tax_year):
            grouped.setdefault(slip.recipient_identity_number, []).append(slip)
        items = [_summarize(form, tax_year, slips) for _, slips in sorted(grouped.items())]
        return A1A2ListResponse(items=items, total=len(items))

    @router.get("/{identity}", response_model=A1A2Detail, dependencies=[_roles])
    def get_aggregate(
        identity: str,
        current_user: CurrentUser,
        session: SessionDep,
        tax_year: Annotated[int, Query(ge=2020, le=2100)],
    ) -> A1A2Detail:
        slips = [
            slip
            for slip in _year_slips(current_user, session, tax_year)
            if slip.recipient_identity_number == identity
        ]
        if not slips:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{form} untuk {identity} tahun {tax_year} tidak ditemukan",
            )
        summary = _summarize(form, tax_year, slips)
        rows = [
            A1A2MonthlyRow(
                tax_month=slip.tax_month,
                gross_income=slip.gross_income,
                income_tax=slip.income_tax,
                withholding_number=slip.withholding_number,
            )
            for slip in sorted(slips, key=lambda slip: slip.tax_month)
        ]
        return A1A2Detail(**summary.model_dump(), rows=rows)

    return router


a1_router = make_a1_a2_router("A1")
a2_router = make_a1_a2_router("A2")
