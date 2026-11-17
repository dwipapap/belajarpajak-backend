"""Display status ("Status Faktur") for e-Faktur lists.

The reference portal filters invoices by a 12-value status. Our storage keeps a
simpler lifecycle (``status`` draft/issued/invalid, ``credit_status`` for
Pajak Masukan, ``spt_flag`` once an SPT Masa is filed), so the display status
is *derived* here, once, for both the list payload and the server-side filter.

Mapping (first match wins):

  Pajak Keluaran (TaxInvoice)
    draft                                   -> CREATED
    issued, spt_flag set                    -> REPORTED
    issued, kind = dibatalkan               -> CANCELED
    issued                                  -> APPROVED
    invalid                                 -> CANCELED

  Pajak Masukan (TaxInputInvoice) — a credit decision can be made on a draft
  too, so credit_status outranks the draft/issued distinction.
    invalid                                 -> CANCELED
    spt_flag set                            -> REPORTED
    credit_status = credited                -> CREDITED
    credit_status = uncredited              -> UNCREDITED
    draft                                   -> CREATED
    issued (not_credited)                   -> APPROVED

AMENDED, DELETED, SAVED_INVALID, SIGNING_IN_PROGRESS, WAITING_FOR_AMENDMENT and
WAITING_FOR_CANCELLATION exist in the reference but have no counterpart in the
simulator (deleted drafts are removed, signing is instant, no amendment
workflow); they are valid filter values that simply match nothing.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import and_, false, or_

from app.models.enums import TaxInputCreditStatus, TaxInvoiceKind, TaxInvoiceStatus


class StatusFaktur(StrEnum):
    AMENDED = "AMENDED"
    APPROVED = "APPROVED"
    CANCELED = "CANCELED"
    CREATED = "CREATED"
    CREDITED = "CREDITED"
    DELETED = "DELETED"
    REPORTED = "REPORTED"
    SAVED_INVALID = "SAVED_INVALID"
    SIGNING_IN_PROGRESS = "SIGNING_IN_PROGRESS"
    UNCREDITED = "UNCREDITED"
    WAITING_FOR_AMENDMENT = "WAITING_FOR_AMENDMENT"
    WAITING_FOR_CANCELLATION = "WAITING_FOR_CANCELLATION"


def keluaran_status_faktur(invoice) -> StatusFaktur:
    if invoice.status == TaxInvoiceStatus.draft:
        return StatusFaktur.CREATED
    if invoice.status == TaxInvoiceStatus.invalid:
        return StatusFaktur.CANCELED
    if invoice.spt_flag is not None:
        return StatusFaktur.REPORTED
    if invoice.invoice_kind == TaxInvoiceKind.dibatalkan:
        return StatusFaktur.CANCELED
    return StatusFaktur.APPROVED


def masukan_status_faktur(invoice) -> StatusFaktur:
    if invoice.status == TaxInvoiceStatus.invalid:
        return StatusFaktur.CANCELED
    if invoice.spt_flag is not None:
        return StatusFaktur.REPORTED
    if invoice.credit_status == TaxInputCreditStatus.credited:
        return StatusFaktur.CREDITED
    if invoice.credit_status == TaxInputCreditStatus.uncredited:
        return StatusFaktur.UNCREDITED
    if invoice.status == TaxInvoiceStatus.draft:
        return StatusFaktur.CREATED
    return StatusFaktur.APPROVED


def keluaran_status_condition(model, statuses: list[StatusFaktur]):
    """SQL filter equivalent to ``keluaran_status_faktur(row) in statuses``."""
    issued = model.status == TaxInvoiceStatus.issued
    cancelled_kind = model.invoice_kind == TaxInvoiceKind.dibatalkan
    parts = {
        StatusFaktur.CREATED: model.status == TaxInvoiceStatus.draft,
        StatusFaktur.CANCELED: or_(
            model.status == TaxInvoiceStatus.invalid,
            and_(issued, model.spt_flag.is_(None), cancelled_kind),
        ),
        StatusFaktur.REPORTED: and_(issued, model.spt_flag.is_not(None)),
        StatusFaktur.APPROVED: and_(issued, model.spt_flag.is_(None), ~cancelled_kind),
    }
    return or_(false(), *(parts.get(s, false()) for s in statuses))


def masukan_status_condition(model, statuses: list[StatusFaktur]):
    """SQL filter equivalent to ``masukan_status_faktur(row) in statuses``."""
    alive = and_(model.status != TaxInvoiceStatus.invalid, model.spt_flag.is_(None))
    not_credited = model.credit_status == TaxInputCreditStatus.not_credited
    parts = {
        StatusFaktur.CANCELED: model.status == TaxInvoiceStatus.invalid,
        StatusFaktur.REPORTED: and_(
            model.status != TaxInvoiceStatus.invalid, model.spt_flag.is_not(None)
        ),
        StatusFaktur.CREDITED: and_(alive, model.credit_status == TaxInputCreditStatus.credited),
        StatusFaktur.UNCREDITED: and_(
            alive, model.credit_status == TaxInputCreditStatus.uncredited
        ),
        StatusFaktur.CREATED: and_(alive, not_credited, model.status == TaxInvoiceStatus.draft),
        StatusFaktur.APPROVED: and_(alive, not_credited, model.status == TaxInvoiceStatus.issued),
    }
    return or_(false(), *(parts.get(s, false()) for s in statuses))
