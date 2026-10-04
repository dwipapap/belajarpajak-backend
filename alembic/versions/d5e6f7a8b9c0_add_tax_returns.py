"""add tax returns and billing codes (SPT Masa)

Revision ID: d5e6f7a8b9c0
Revises: c3d4e5f6a7b8
Create Date: 2026-10-04 12:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel  # noqa: F401
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

tax_return_type = postgresql.ENUM(
    "ppn", "pph_unifikasi", name="tax_return_type", create_type=False
)
tax_return_kind = postgresql.ENUM("normal", "pembetulan", name="tax_return_kind", create_type=False)
tax_return_status = postgresql.ENUM(
    "draft", "awaiting_payment", "reported", name="tax_return_status", create_type=False
)
signer_capacity = postgresql.ENUM(
    "wajib_pajak", "kuasa_wajib_pajak", name="signer_capacity", create_type=False
)
billing_status = postgresql.ENUM("unpaid", "paid", name="billing_status", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in (tax_return_type, tax_return_kind, tax_return_status, signer_capacity, billing_status):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "tax_returns",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=True),
        sa.Column("siswa_id", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("return_type", tax_return_type, nullable=False),
        sa.Column("kind", tax_return_kind, nullable=False),
        sa.Column("correction_no", sa.Integer(), nullable=False),
        sa.Column("status", tax_return_status, nullable=False),
        sa.Column("tax_month", sa.Integer(), nullable=False),
        sa.Column("tax_year", sa.Integer(), nullable=False),
        sa.Column("signer_npwp", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True),
        sa.Column("signer_name", sqlmodel.sql.sqltypes.AutoString(length=150), nullable=True),
        sa.Column("signer_capacity", signer_capacity, nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_by_id", sa.Integer(), nullable=True),
        sa.Column("penyerahan_dpp", sa.BigInteger(), nullable=False),
        sa.Column("penyerahan_dpp_nilai_lain", sa.BigInteger(), nullable=False),
        sa.Column("penyerahan_ppn", sa.BigInteger(), nullable=False),
        sa.Column("penyerahan_ppnbm", sa.BigInteger(), nullable=False),
        sa.Column("masukan_dpp", sa.BigInteger(), nullable=False),
        sa.Column("masukan_dpp_nilai_lain", sa.BigInteger(), nullable=False),
        sa.Column("masukan_ppn", sa.BigInteger(), nullable=False),
        sa.Column("kompensasi_lebih_bayar", sa.BigInteger(), nullable=False),
        sa.Column("ppn_kurang_bayar", sa.BigInteger(), nullable=False),
        sa.Column("ppn_lebih_bayar", sa.BigInteger(), nullable=False),
        sa.Column("total_income_tax", sa.BigInteger(), nullable=False),
        sa.Column("amount_due", sa.BigInteger(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("teacher_feedback", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=True),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["siswa_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["submitted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "siswa_id",
            "return_type",
            "tax_year",
            "tax_month",
            name="uq_spt_tenant_siswa_type_period",
        ),
    )
    op.create_index(op.f("ix_tax_returns_class_id"), "tax_returns", ["class_id"])
    op.create_index(op.f("ix_tax_returns_created_by_id"), "tax_returns", ["created_by_id"])
    op.create_index(op.f("ix_tax_returns_return_type"), "tax_returns", ["return_type"])
    op.create_index(op.f("ix_tax_returns_siswa_id"), "tax_returns", ["siswa_id"])
    op.create_index(op.f("ix_tax_returns_status"), "tax_returns", ["status"])
    op.create_index(op.f("ix_tax_returns_tax_month"), "tax_returns", ["tax_month"])
    op.create_index(op.f("ix_tax_returns_tax_year"), "tax_returns", ["tax_year"])
    op.create_index(op.f("ix_tax_returns_tenant_id"), "tax_returns", ["tenant_id"])

    op.create_table(
        "tax_return_lines",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("return_id", sa.Integer(), nullable=False),
        sa.Column("section", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column("code", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("dpp", sa.BigInteger(), nullable=False),
        sa.Column("dpp_nilai_lain", sa.BigInteger(), nullable=False),
        sa.Column("ppn", sa.BigInteger(), nullable=False),
        sa.Column("ppnbm", sa.BigInteger(), nullable=False),
        sa.Column("setor_sendiri", sa.BigInteger(), nullable=False),
        sa.Column("pemotongan", sa.BigInteger(), nullable=False),
        sa.Column("ditanggung_pemerintah", sa.BigInteger(), nullable=False),
        sa.Column("dibayar_dari_spt_dibetulkan", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["return_id"], ["tax_returns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tax_return_lines_return_id"), "tax_return_lines", ["return_id"])
    op.create_index(op.f("ix_tax_return_lines_section"), "tax_return_lines", ["section"])

    op.create_table(
        "billing_codes",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("siswa_id", sa.Integer(), nullable=False),
        sa.Column("spt_id", sa.Integer(), nullable=True),
        sa.Column("code", sqlmodel.sql.sqltypes.AutoString(length=15), nullable=False),
        sa.Column("status", billing_status, nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("kap_kjs", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column("masa_pajak", sqlmodel.sql.sqltypes.AutoString(length=6), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["spt_id"], ["tax_returns.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["siswa_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_billing_tenant_code"),
    )
    op.create_index(op.f("ix_billing_codes_code"), "billing_codes", ["code"])
    op.create_index(op.f("ix_billing_codes_siswa_id"), "billing_codes", ["siswa_id"])
    op.create_index(op.f("ix_billing_codes_spt_id"), "billing_codes", ["spt_id"])
    op.create_index(op.f("ix_billing_codes_status"), "billing_codes", ["status"])
    op.create_index(op.f("ix_billing_codes_tenant_id"), "billing_codes", ["tenant_id"])

    # Source documents gain the SPT reporting flag (reuses the existing
    # slip_spt_flag enum type — no new type is created).
    spt_flag_enum = postgresql.ENUM(
        "reported_in_spt",
        "objection_in_progress",
        "objection_completed",
        "objection_rejected_formal",
        "objection_withdrawal_review",
        "objection_withdrawal_accepted",
        "spt_audited",
        "spt_legal_process",
        name="slip_spt_flag",
        create_type=False,
    )
    op.add_column(
        "tax_invoices",
        sa.Column("spt_flag", spt_flag_enum, nullable=True),
    )
    op.create_index(op.f("ix_tax_invoices_spt_flag"), "tax_invoices", ["spt_flag"])
    op.add_column(
        "tax_input_invoices",
        sa.Column("spt_flag", spt_flag_enum, nullable=True),
    )
    op.create_index(op.f("ix_tax_input_invoices_spt_flag"), "tax_input_invoices", ["spt_flag"])


def downgrade() -> None:
    op.drop_index(op.f("ix_tax_input_invoices_spt_flag"), table_name="tax_input_invoices")
    op.drop_column("tax_input_invoices", "spt_flag")
    op.drop_index(op.f("ix_tax_invoices_spt_flag"), table_name="tax_invoices")
    op.drop_column("tax_invoices", "spt_flag")

    op.drop_index(op.f("ix_billing_codes_tenant_id"), table_name="billing_codes")
    op.drop_index(op.f("ix_billing_codes_status"), table_name="billing_codes")
    op.drop_index(op.f("ix_billing_codes_spt_id"), table_name="billing_codes")
    op.drop_index(op.f("ix_billing_codes_siswa_id"), table_name="billing_codes")
    op.drop_index(op.f("ix_billing_codes_code"), table_name="billing_codes")
    op.drop_table("billing_codes")

    op.drop_index(op.f("ix_tax_return_lines_section"), table_name="tax_return_lines")
    op.drop_index(op.f("ix_tax_return_lines_return_id"), table_name="tax_return_lines")
    op.drop_table("tax_return_lines")

    op.drop_index(op.f("ix_tax_returns_tenant_id"), table_name="tax_returns")
    op.drop_index(op.f("ix_tax_returns_tax_year"), table_name="tax_returns")
    op.drop_index(op.f("ix_tax_returns_tax_month"), table_name="tax_returns")
    op.drop_index(op.f("ix_tax_returns_status"), table_name="tax_returns")
    op.drop_index(op.f("ix_tax_returns_siswa_id"), table_name="tax_returns")
    op.drop_index(op.f("ix_tax_returns_return_type"), table_name="tax_returns")
    op.drop_index(op.f("ix_tax_returns_created_by_id"), table_name="tax_returns")
    op.drop_index(op.f("ix_tax_returns_class_id"), table_name="tax_returns")
    op.drop_table("tax_returns")

    bind = op.get_bind()
    for enum_type in (billing_status, signer_capacity, tax_return_status, tax_return_kind, tax_return_type):
        enum_type.drop(bind, checkfirst=True)
