"""010: nullable slip counterparty for SP/CY

Revision ID: e010sliprecipient
Revises: d008prepopulated
Create Date: 2026-11-11

Penyetoran Sendiri (SP) and Penyetoran secara Digunggung (CY) have no
counterparty, so ``recipient_identity_number`` and ``recipient_name`` become
nullable. Other slip types still require them at the API layer.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e010sliprecipient'
down_revision: str | None = 'd008prepopulated'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        'withholding_slips', 'recipient_identity_number',
        existing_type=sa.String(length=32), nullable=True,
    )
    op.alter_column(
        'withholding_slips', 'recipient_name',
        existing_type=sa.String(length=150), nullable=True,
    )


def downgrade() -> None:
    op.execute(
        "UPDATE withholding_slips SET recipient_identity_number = '0000000000000000' "
        "WHERE recipient_identity_number IS NULL"
    )
    op.execute("UPDATE withholding_slips SET recipient_name = '-' WHERE recipient_name IS NULL")
    op.alter_column(
        'withholding_slips', 'recipient_name',
        existing_type=sa.String(length=150), nullable=False,
    )
    op.alter_column(
        'withholding_slips', 'recipient_identity_number',
        existing_type=sa.String(length=32), nullable=False,
    )
