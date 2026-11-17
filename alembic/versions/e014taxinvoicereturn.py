"""Retur Pajak Masukan: tax_invoice_returns and tax_invoice_return_lines.

Revision ID: e014taxinvoicereturn
Revises: e013bpnrforeign
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e014taxinvoicereturn"
down_revision: str | None = "e013bpnrforeign"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    status = postgresql.ENUM(
        "draft", "issued", "invalid", name="tax_invoice_status", create_type=False
    )
    op.create_table(
        "tax_invoice_returns",
        *_ts(),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=True),
        sa.Column("siswa_id", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("input_invoice_id", sa.Integer(), nullable=False),
        sa.Column("status", status, nullable=False),
        sa.Column("return_number", sa.String(length=60), nullable=False),
        sa.Column("return_date", sa.Date(), nullable=False),
        sa.Column("tax_month", sa.Integer(), nullable=False),
        sa.Column("tax_year", sa.Integer(), nullable=False),
        sa.Column("is_creditable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "electronic_signature_status",
            sa.String(length=40),
            nullable=False,
            server_default="not_signed",
        ),
        sa.Column("signer_id", sa.Integer(), nullable=True),
        sa.Column("signer_name", sa.String(length=150), nullable=True),
        sa.Column("total_dpp", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_dpp_other", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_ppn", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_ppnbm", sa.BigInteger(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["siswa_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["input_invoice_id"], ["tax_input_invoices.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["signer_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "siswa_id", "return_number", name="uq_tax_returns_number"),
    )
    for column in (
        "tenant_id",
        "class_id",
        "siswa_id",
        "created_by_id",
        "input_invoice_id",
        "status",
        "return_number",
        "tax_month",
        "tax_year",
        "signer_id",
    ):
        op.create_index(op.f(f"ix_tax_invoice_returns_{column}"), "tax_invoice_returns", [column])

    op.create_table(
        "tax_invoice_return_lines",
        *_ts(),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("return_id", sa.Integer(), nullable=False),
        sa.Column("input_line_id", sa.Integer(), nullable=False),
        sa.Column("line_order", sa.Integer(), nullable=False),
        sa.Column("return_quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("return_discount", sa.BigInteger(), nullable=False),
        sa.Column("return_total_price", sa.BigInteger(), nullable=False),
        sa.Column("dpp", sa.BigInteger(), nullable=False),
        sa.Column("dpp_other", sa.BigInteger(), nullable=False),
        sa.Column("ppn", sa.BigInteger(), nullable=False),
        sa.Column("ppnbm", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["return_id"], ["tax_invoice_returns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["input_line_id"], ["tax_input_lines.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_tax_invoice_return_lines_return_id"), "tax_invoice_return_lines", ["return_id"]
    )
    op.create_index(
        op.f("ix_tax_invoice_return_lines_input_line_id"),
        "tax_invoice_return_lines",
        ["input_line_id"],
    )


def downgrade() -> None:
    op.drop_table("tax_invoice_return_lines")
    op.drop_table("tax_invoice_returns")
