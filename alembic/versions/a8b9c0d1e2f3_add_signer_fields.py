"""add signer fields to withholding_slips

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-10-18 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8b9c0d1e2f3"
down_revision: str | None = "f7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("withholding_slips", sa.Column("signer_id", sa.Integer(), nullable=True))
    op.create_index("ix_withholding_slips_signer_id", "withholding_slips", ["signer_id"])
    op.add_column("withholding_slips", sa.Column("signer_name", sa.String(150), nullable=True))


def downgrade() -> None:
    op.drop_column("withholding_slips", "signer_name")
    op.drop_index("ix_withholding_slips_signer_id", table_name="withholding_slips")
    op.drop_column("withholding_slips", "signer_id")
