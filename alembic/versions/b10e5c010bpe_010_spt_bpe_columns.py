"""010: SPT BPE columns (bpe_number, received_at)

Revision ID: b10e5c010bpe
Revises: fccbed185414
Create Date: 2026-11-01

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel  # noqa: F401

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b10e5c010bpe'
down_revision: str | None = 'fccbed185414'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'tax_returns',
        sa.Column('bpe_number', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=True),
    )
    op.add_column(
        'tax_returns', sa.Column('received_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.create_unique_constraint('uq_tax_returns_bpe_number', 'tax_returns', ['bpe_number'])
    # Backfill already-reported SPTs (ad-hoc dev data; seed only creates drafts):
    # per-tenant-per-year sequence, same format as mark_reported uses.
    op.execute(
        sa.text(
            """
            WITH numbered AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY tenant_id, tax_year ORDER BY id
                       ) AS rn
                FROM tax_returns
                WHERE status = 'reported'
            )
            UPDATE tax_returns AS t
            SET bpe_number = 'BPE-SIM-' || lpad(numbered.rn::text, 5, '0')
                                 || '/' || t.tax_year,
                received_at = COALESCE(t.received_at, t.submitted_at)
            FROM numbered
            WHERE numbered.id = t.id
            """
        )
    )


def downgrade() -> None:
    op.drop_constraint('uq_tax_returns_bpe_number', 'tax_returns', type_='unique')
    op.drop_column('tax_returns', 'received_at')
    op.drop_column('tax_returns', 'bpe_number')
