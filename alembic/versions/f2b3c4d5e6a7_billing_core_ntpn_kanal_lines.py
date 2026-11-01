"""billing core A2+A3+A4+B4: ntpn/bpn/kanal/tagihan/metode + billing_lines

Revision ID: f2b3c4d5e6a7
Revises: e7a1b2c3d4f5
Create Date: 2026-11-01 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2b3c4d5e6a7"
down_revision: str | None = "e7a1b2c3d4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("billing_codes", sa.Column("ntpn", sa.String(16), nullable=True))
    op.add_column("billing_codes", sa.Column("no_bpn", sa.String(32), nullable=True))
    op.add_column(
        "billing_codes",
        sa.Column("kanal", sa.String(20), nullable=False, server_default="teller"),
    )
    op.add_column("billing_codes", sa.Column("tagihan_id", sa.Integer(), nullable=True))
    op.add_column(
        "billing_codes",
        sa.Column("metode", sa.String(20), nullable=False, server_default="billing"),
    )
    op.create_index("ix_billing_codes_tagihan_id", "billing_codes", ["tagihan_id"])
    op.create_table(
        "billing_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "billing_id",
            sa.Integer(),
            sa.ForeignKey("billing_codes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("kap_kjs", sa.String(20), nullable=False),
        sa.Column("masa_pajak", sa.String(6), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("billing_lines")
    op.drop_index("ix_billing_codes_tagihan_id", table_name="billing_codes")
    op.drop_column("billing_codes", "metode")
    op.drop_column("billing_codes", "tagihan_id")
    op.drop_column("billing_codes", "kanal")
    op.drop_column("billing_codes", "no_bpn")
    op.drop_column("billing_codes", "ntpn")
