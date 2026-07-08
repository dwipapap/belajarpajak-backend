"""add treaty/P3B columns to withholding_slips

Revision ID: c8d3e5f0b42a
Revises: b7c2d4e9a31f
Create Date: 2026-07-08 22:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8d3e5f0b42a"
down_revision: str | None = "b7c2d4e9a31f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("withholding_slips", sa.Column("negara_treaty", sa.String(5), nullable=True))
    op.add_column("withholding_slips", sa.Column("pasal_treaty", sa.String(20), nullable=True))
    op.add_column("withholding_slips", sa.Column("nomor_skd", sa.String(100), nullable=True))
    op.add_column(
        "withholding_slips",
        sa.Column("tarif_treaty_basis_points", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("withholding_slips", "tarif_treaty_basis_points")
    op.drop_column("withholding_slips", "nomor_skd")
    op.drop_column("withholding_slips", "pasal_treaty")
    op.drop_column("withholding_slips", "negara_treaty")
