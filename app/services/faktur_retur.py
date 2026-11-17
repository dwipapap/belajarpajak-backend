"""Retur Pajak Masukan engine and SPT selectors.

A retur recomputes DPP / DPP Nilai Lain / PPN / PPnBM from the *returned*
quantity with the original line's rates, using the same integer-rupiah rules as
the faktur engines (11/12 for DPP Nilai Lain, ROUND_HALF_UP).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from sqlmodel import Session, func, select

from app.core.money import apply_basis_points
from app.models.enums import TaxInvoiceStatus
from app.models.tax_input_invoice import TaxInputInvoice, TaxInputLine
from app.models.tax_invoice import TaxInvoice
from app.models.tax_invoice_return import TaxInvoiceReturn, TaxInvoiceReturnLine


def compute_return_line(
    original: TaxInputLine, *, quantity_milli: int, discount: int
) -> dict[str, int]:
    """Derived amounts of one returned line (pure function)."""
    total = Decimal(original.unit_price) * Decimal(quantity_milli) / Decimal("1000")
    total_price = int(total.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    dpp = max(total_price - discount, 0)
    if original.use_dpp_other:
        other = int(
            (Decimal(dpp) * Decimal("11") / Decimal("12")).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        ppn = apply_basis_points(other, original.ppn_rate_basis_points)
    else:
        other = 0
        ppn = apply_basis_points(dpp, original.ppn_rate_basis_points)
    return {
        "return_total_price": total_price,
        "dpp": dpp,
        "dpp_other": other,
        "ppn": ppn,
        "ppnbm": apply_basis_points(dpp, original.ppnbm_rate_basis_points),
    }


def already_returned_milli(
    session: Session, input_line_id: int, *, exclude_return_id: int | None = None
) -> int:
    """Quantity of an original line covered by other *issued* returns."""
    query = (
        select(func.coalesce(func.sum(TaxInvoiceReturnLine.return_quantity_milli), 0))
        .join(TaxInvoiceReturn, TaxInvoiceReturn.id == TaxInvoiceReturnLine.return_id)
        .where(
            TaxInvoiceReturnLine.input_line_id == input_line_id,
            TaxInvoiceReturn.status == TaxInvoiceStatus.issued,
        )
    )
    if exclude_return_id is not None:
        query = query.where(TaxInvoiceReturn.id != exclude_return_id)
    return int(session.exec(query).one())


def _issued_returns_in_period(session: Session, *, tenant_id: int, tax_year: int, tax_month: int):
    return (
        select(TaxInvoiceReturn, TaxInputInvoice)
        .join(TaxInputInvoice, TaxInputInvoice.id == TaxInvoiceReturn.input_invoice_id)
        .where(
            TaxInvoiceReturn.tenant_id == tenant_id,
            TaxInvoiceReturn.status == TaxInvoiceStatus.issued,
            TaxInvoiceReturn.tax_year == tax_year,
            TaxInvoiceReturn.tax_month == tax_month,
        )
    )


def select_retur_masukan(
    session: Session, *, tenant_id: int, siswa_id: int, tax_year: int, tax_month: int
) -> list[tuple[TaxInvoiceReturn, TaxInputInvoice]]:
    """Issued, creditable returns the siswa made as buyer in the period."""
    query = _issued_returns_in_period(
        session, tenant_id=tenant_id, tax_year=tax_year, tax_month=tax_month
    ).where(TaxInvoiceReturn.siswa_id == siswa_id, TaxInvoiceReturn.is_creditable.is_(True))
    return [(r, i) for r, i in session.exec(query).all()]


def select_retur_keluaran(
    session: Session, *, tenant_id: int, siswa_id: int, tax_year: int, tax_month: int
) -> list[tuple[TaxInvoiceReturn, TaxInputInvoice]]:
    """Issued returns received by the siswa as seller (via the keluaran source)."""
    query = (
        _issued_returns_in_period(
            session, tenant_id=tenant_id, tax_year=tax_year, tax_month=tax_month
        )
        .join(TaxInvoice, TaxInvoice.id == TaxInputInvoice.source_invoice_id)
        .where(TaxInvoice.siswa_id == siswa_id)
    )
    return [(r, i) for r, i in session.exec(query).all()]
