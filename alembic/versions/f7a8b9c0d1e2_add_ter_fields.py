"""add ter_category and calculation_method to withholding_slips

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-10-18 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7a8b9c0d1e2"
down_revision: str | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("withholding_slips", sa.Column("ter_category", sa.String(1), nullable=True))
    op.add_column(
        "withholding_slips", sa.Column("calculation_method", sa.String(20), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("withholding_slips", "calculation_method")
    op.drop_column("withholding_slips", "ter_category")
