"""Shared helpers to build a Pajak Masukan invoice from a Keluaran source.

Extracted from the ``source_invoice_id`` branch of the masukan create route so
the manual copy path and the automatic prepopulate-on-issue path use one
implementation. No database side effects here: callers attach the returned
objects to their own session/transaction.
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.models.tax_input_invoice import TaxInputInvoice, TaxInputLine
from app.models.tax_invoice import TaxInvoice, TaxInvoiceLine


def list_source_lines(session: Session, source_id: int) -> list[TaxInvoiceLine]:
    """Detail Transaksi rows of a keluaran source, in line order."""
    return list(
        session.exec(
            select(TaxInvoiceLine)
            .where(TaxInvoiceLine.invoice_id == source_id)
            .order_by(TaxInvoiceLine.line_order)
        ).all()
    )


def build_input_line_from_source(
    source_line: TaxInvoiceLine, *, invoice_id: int, line_order: int
) -> TaxInputLine:
    """Map one keluaran line onto a masukan line (same amounts, same rates)."""
    return TaxInputLine(
        invoice_id=invoice_id,
        line_order=line_order,
        line_type=source_line.line_type,
        item_code=source_line.item_code,
        item_name=source_line.item_name,
        unit=source_line.unit,
        unit_price=source_line.unit_price,
        quantity_milli=source_line.quantity_milli,
        discount=source_line.discount,
        use_dpp_other=source_line.use_dpp_other,
        ppn_rate_basis_points=source_line.ppn_rate_basis_points,
        ppnbm_rate_basis_points=source_line.ppnbm_rate_basis_points,
    )


def apply_source_header(
    target: TaxInputInvoice,
    source: TaxInvoice,
    payload: dict,
) -> TaxInputInvoice:
    """Fill empty seller/invoice-number fields of a masukan draft from its source."""
    if not payload.get("invoice_number"):
        target.invoice_number = source.invoice_number
    if not payload.get("seller_name"):
        target.seller_name = source.seller_name
    if not payload.get("seller_npwp"):
        target.seller_npwp = source.seller_npwp
    if not payload.get("seller_address"):
        target.seller_address = source.seller_address
    if not payload.get("seller_idtku"):
        target.seller_idtku = source.seller_idtku
    return target
