"""change batas_bawah and batas_atas to BIGINT

Revision ID: 25c389d80451
Revises: c8d3e5f0b42a
Create Date: 2026-07-08 21:15:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "25c389d80451"
down_revision: str | None = "c8d3e5f0b42a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "tarif_progresif",
        "batas_bawah",
        type_=sa.BigInteger(),
        existing_type=sa.Integer(),
        existing_nullable=False,
    )
    op.alter_column(
        "tarif_progresif",
        "batas_atas",
        type_=sa.BigInteger(),
        existing_type=sa.Integer(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "tarif_progresif",
        "batas_atas",
        type_=sa.Integer(),
        existing_type=sa.BigInteger(),
        existing_nullable=True,
    )
    op.alter_column(
        "tarif_progresif",
        "batas_bawah",
        type_=sa.Integer(),
        existing_type=sa.BigInteger(),
        existing_nullable=False,
    )
