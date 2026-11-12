"""SPT Masa document PDFs (Induk) — dispatch per return type.

Layouts mirror the standard SPT Masa forms (PPN, PPh Unifikasi, PPh 21/26) on the
shared ``pdf_kit`` look. Values come from the posted ``TaxReturn`` header and
its ``TaxReturnLine`` snapshot; rows the simulator does not track print as 0.
Branding rule: neutral simulator letterhead only.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.models.enums import TaxReturnType
from app.models.tax_return import TaxReturn, TaxReturnLine
from app.services.spt_pdf_pph import render_pph21_26, render_unifikasi
from app.services.spt_pdf_ppn import render_ppn


def render_spt_pdf(
    tax_return: TaxReturn,
    lines: Sequence[TaxReturnLine],
    tenant_name: str | None = None,
    taxpayer_name: str | None = None,
) -> bytes:
    """Render the SPT Induk document as PDF bytes."""
    name = (taxpayer_name or tenant_name or "-").upper()
    if tax_return.return_type == TaxReturnType.ppn:
        return render_ppn(tax_return, lines, tenant_name, name)
    if tax_return.return_type == TaxReturnType.pph_unifikasi:
        return render_unifikasi(tax_return, lines, tenant_name, name)
    return render_pph21_26(tax_return, lines, tenant_name, name)
