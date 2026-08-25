"""migrate NSFP 16-digit to 17-digit per PER-11/PJ/2025 Pasal 37

Revision ID: b2c17d8e3a01
Revises: a1f6c02d7b84
Create Date: 2026-08-25 00:00:00.000000

17-digit NSFP = {kode_transaksi:2}{kode_status:2}{tahun:2}{urut:11}
kode_status 00=normal, 01=pengganti ke-1, 02=ke-2 ...
urut 11-digit per tenant per tahun, auto-assigned on issue.

upgrade(): nullify legacy 16-digit numbers so they regenerate as 17-digit on
next issue. Downgrade is a no-op — 16-digit cannot be reconstructed reliably.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c17d8e3a01"
down_revision: str | None = "a1f6c02d7b84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE tax_invoices SET invoice_number = NULL "
        "WHERE invoice_number IS NOT NULL AND length(invoice_number) = 16"
    )


def downgrade() -> None:
    # No-op: 16-digit format cannot be reliably reconstructed from 17-digit
    # (status width changed 1->2 digits, serial width 8->11 digits).
    pass
