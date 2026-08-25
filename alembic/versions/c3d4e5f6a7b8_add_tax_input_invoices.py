"""add tax input invoices (pajak masukan)

Revision ID: c3d4e5f6a7b8
Revises: b2c17d8e3a01
Create Date: 2026-08-25 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel  # noqa: F401
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c17d8e3a01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

tax_invoice_status = postgresql.ENUM(
    "draft", "issued", "invalid", name="tax_invoice_status", create_type=False
)
tax_invoice_kind = postgresql.ENUM(
    "normal", "pengganti", "dibatalkan", name="tax_invoice_kind", create_type=False
)
tax_invoice_line_type = postgresql.ENUM(
    "barang", "jasa", name="tax_invoice_line_type", create_type=False
)
buyer_identity_type = postgresql.ENUM(
    "npwp", "nik", "paspor", "identitas_lain", name="buyer_identity_type", create_type=False
)
tax_input_credit_status = postgresql.ENUM(
    "not_credited", "credited", name="tax_input_credit_status", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    tax_input_credit_status.create(bind, checkfirst=True)

    op.create_table(
        "tax_input_invoices",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=True),
        sa.Column("siswa_id", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("status", tax_invoice_status, nullable=False),
        sa.Column("invoice_number", sqlmodel.sql.sqltypes.AutoString(length=24), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalid_reason", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True),
        sa.Column("electronic_signature_status", sqlmodel.sql.sqltypes.AutoString(length=40), nullable=False),
        sa.Column("credit_status", tax_input_credit_status, nullable=False),
        sa.Column("credited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_invoice_id", sa.Integer(), nullable=True),
        sa.Column("is_advance_payment", sa.Boolean(), nullable=False),
        sa.Column("is_settlement", sa.Boolean(), nullable=False),
        sa.Column("transaction_code", sqlmodel.sql.sqltypes.AutoString(length=2), nullable=False),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("invoice_kind", tax_invoice_kind, nullable=False),
        sa.Column("tax_month", sa.Integer(), nullable=False),
        sa.Column("tax_year", sa.Integer(), nullable=False),
        sa.Column("reference", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("seller_npwp", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True),
        sa.Column("seller_name", sqlmodel.sql.sqltypes.AutoString(length=150), nullable=True),
        sa.Column("seller_address", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("seller_idtku", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True),
        sa.Column("buyer_identity_type", buyer_identity_type, nullable=False),
        sa.Column("buyer_identity_number", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("buyer_country", sqlmodel.sql.sqltypes.AutoString(length=2), nullable=False),
        sa.Column("buyer_document_number", sqlmodel.sql.sqltypes.AutoString(length=60), nullable=True),
        sa.Column("buyer_name", sqlmodel.sql.sqltypes.AutoString(length=150), nullable=False),
        sa.Column("buyer_address", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("buyer_idtku", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True),
        sa.Column("buyer_email", sqlmodel.sql.sqltypes.AutoString(length=150), nullable=True),
        sa.Column("total_price", sa.BigInteger(), nullable=False),
        sa.Column("total_discount", sa.BigInteger(), nullable=False),
        sa.Column("total_dpp", sa.BigInteger(), nullable=False),
        sa.Column("total_dpp_other", sa.BigInteger(), nullable=False),
        sa.Column("total_ppn", sa.BigInteger(), nullable=False),
        sa.Column("total_ppnbm", sa.BigInteger(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("teacher_feedback", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["siswa_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_invoice_id"], ["tax_invoices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "invoice_number", name="uq_tax_input_tenant_number"),
    )
    op.create_index(op.f("ix_tax_input_invoices_class_id"), "tax_input_invoices", ["class_id"])
    op.create_index(op.f("ix_tax_input_invoices_created_by_id"), "tax_input_invoices", ["created_by_id"])
    op.create_index(op.f("ix_tax_input_invoices_credit_status"), "tax_input_invoices", ["credit_status"])
    op.create_index(op.f("ix_tax_input_invoices_invoice_number"), "tax_input_invoices", ["invoice_number"])
    op.create_index(op.f("ix_tax_input_invoices_siswa_id"), "tax_input_invoices", ["siswa_id"])
    op.create_index(op.f("ix_tax_input_invoices_source_invoice_id"), "tax_input_invoices", ["source_invoice_id"])
    op.create_index(op.f("ix_tax_input_invoices_status"), "tax_input_invoices", ["status"])
    op.create_index(op.f("ix_tax_input_invoices_tax_month"), "tax_input_invoices", ["tax_month"])
    op.create_index(op.f("ix_tax_input_invoices_tax_year"), "tax_input_invoices", ["tax_year"])
    op.create_index(op.f("ix_tax_input_invoices_tenant_id"), "tax_input_invoices", ["tenant_id"])
    op.create_index(op.f("ix_tax_input_invoices_transaction_code"), "tax_input_invoices", ["transaction_code"])

    op.create_table(
        "tax_input_lines",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("line_order", sa.Integer(), nullable=False),
        sa.Column("line_type", tax_invoice_line_type, nullable=False),
        sa.Column("item_code", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=True),
        sa.Column("item_name", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("unit", sqlmodel.sql.sqltypes.AutoString(length=40), nullable=False),
        sa.Column("unit_price", sa.BigInteger(), nullable=False),
        sa.Column("quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("total_price", sa.BigInteger(), nullable=False),
        sa.Column("discount", sa.BigInteger(), nullable=False),
        sa.Column("dpp", sa.BigInteger(), nullable=False),
        sa.Column("use_dpp_other", sa.Boolean(), nullable=False),
        sa.Column("dpp_other", sa.BigInteger(), nullable=False),
        sa.Column("ppn_rate_basis_points", sa.Integer(), nullable=False),
        sa.Column("ppn", sa.BigInteger(), nullable=False),
        sa.Column("ppnbm_rate_basis_points", sa.Integer(), nullable=False),
        sa.Column("ppnbm", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["tax_input_invoices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tax_input_lines_invoice_id"), "tax_input_lines", ["invoice_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_tax_input_lines_invoice_id"), table_name="tax_input_lines")
    op.drop_table("tax_input_lines")

    op.drop_index(op.f("ix_tax_input_invoices_transaction_code"), table_name="tax_input_invoices")
    op.drop_index(op.f("ix_tax_input_invoices_tenant_id"), table_name="tax_input_invoices")
    op.drop_index(op.f("ix_tax_input_invoices_tax_year"), table_name="tax_input_invoices")
    op.drop_index(op.f("ix_tax_input_invoices_tax_month"), table_name="tax_input_invoices")
    op.drop_index(op.f("ix_tax_input_invoices_status"), table_name="tax_input_invoices")
    op.drop_index(op.f("ix_tax_input_invoices_source_invoice_id"), table_name="tax_input_invoices")
    op.drop_index(op.f("ix_tax_input_invoices_siswa_id"), table_name="tax_input_invoices")
    op.drop_index(op.f("ix_tax_input_invoices_invoice_number"), table_name="tax_input_invoices")
    op.drop_index(op.f("ix_tax_input_invoices_credit_status"), table_name="tax_input_invoices")
    op.drop_index(op.f("ix_tax_input_invoices_created_by_id"), table_name="tax_input_invoices")
    op.drop_index(op.f("ix_tax_input_invoices_class_id"), table_name="tax_input_invoices")
    op.drop_table("tax_input_invoices")

    bind = op.get_bind()
    tax_input_credit_status.drop(bind, checkfirst=True)
