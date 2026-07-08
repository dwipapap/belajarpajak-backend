"""add ptkp tiers and progressive tariff tables

Revision ID: b7c2d4e9a31f
Revises: 9e4f7a1c3b20
Create Date: 2026-07-08 21:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c2d4e9a31f"
down_revision: str | None = "9e4f7a1c3b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tarif_ptkp",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status_kode", sa.String(10), nullable=False),
        sa.Column("jumlah_ptkp", sa.Integer(), nullable=False),
        sa.Column("tahun_pajak", sa.Integer(), nullable=False),
        sa.Column("keterangan", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("status_kode", "tahun_pajak"),
    )
    op.create_index(op.f("ix_tarif_ptkp_status_kode"), "tarif_ptkp", ["status_kode"])
    op.create_index(op.f("ix_tarif_ptkp_tahun_pajak"), "tarif_ptkp", ["tahun_pajak"])

    op.create_table(
        "tarif_progresif",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batas_bawah", sa.Integer(), nullable=False),
        sa.Column("batas_atas", sa.Integer(), nullable=True),
        sa.Column("persentase_basis_points", sa.Integer(), nullable=False),
        sa.Column("tahun_pajak", sa.Integer(), nullable=False),
        sa.Column("keterangan", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_tarif_progresif_tahun_pajak"), "tarif_progresif", ["tahun_pajak"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_tarif_progresif_tahun_pajak"), table_name="tarif_progresif")
    op.drop_table("tarif_progresif")
    op.drop_index(op.f("ix_tarif_ptkp_tahun_pajak"), table_name="tarif_ptkp")
    op.drop_index(op.f("ix_tarif_ptkp_status_kode"), table_name="tarif_ptkp")
    op.drop_table("tarif_ptkp")
