"""deposit + pbk + tagihan + angsuran (dompet, pemindahbukuan, ketetapan, cicilan)

Revision ID: d30751b7c001
Revises: e7a1b2c3d4f5
Create Date: 2026-11-01

- deposit_accounts + deposit_mutations (dompet deposit, aturan lunas-sekaligus)
- pbk_requests (persetujuan pemindahbukuan, tanpa gerakan saldo)
- tagihan (SKP/STP; relasi billing disimpan di tagihan.billing_id karena
  tabel billing milik jalur lain dan tidak diubah)
- angsuran + cicilan (cicilan 2-12 atas satu billing unpaid)
- tax_returns.payment_ref (referensi pembayaran non-billing, mis. deposit)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel  # noqa: F401

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d30751b7c001"
down_revision: str | None = "f2b3c4d5e6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deposit_accounts",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("saldo", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_deposit_accounts_user_id", "deposit_accounts", ["user_id"])
    op.create_index("ix_deposit_accounts_tenant_id", "deposit_accounts", ["tenant_id"])

    op.create_table(
        "deposit_mutations",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("jenis", sa.Enum("topup", "pakai", name="deposit_mutation_kind"), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("ref_type", sa.String(length=20), nullable=True),
        sa.Column("ref_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["deposit_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "ref_type", "ref_id", name="uq_deposit_mutation_ref"),
    )
    op.create_index("ix_deposit_mutations_account_id", "deposit_mutations", ["account_id"])

    op.create_table(
        "pbk_requests",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("dari_kap", sa.String(length=20), nullable=False),
        sa.Column("ke_kap", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("masa", sa.String(length=6), nullable=False),
        sa.Column("alasan", sa.String(length=500), nullable=False),
        sa.Column("status", sa.Enum("diajukan", "disetujui", "ditolak", name="pbk_status"), nullable=False, server_default="diajukan"),
        sa.Column("decided_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["decided_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pbk_requests_tenant_id", "pbk_requests", ["tenant_id"])
    op.create_index("ix_pbk_requests_user_id", "pbk_requests", ["user_id"])
    op.create_index("ix_pbk_requests_status", "pbk_requests", ["status"])

    op.create_table(
        "tagihan",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("jenis", sa.Enum("skp", "stp", name="tagihan_jenis"), nullable=False),
        sa.Column("kap_kjs", sa.String(length=20), nullable=False),
        sa.Column("masa", sa.String(length=6), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Enum("belum", "dibilling", "lunas", name="tagihan_status"), nullable=False, server_default="belum"),
        sa.Column("billing_id", sa.Integer(), nullable=True, unique=True),
        sa.ForeignKeyConstraint(["billing_id"], ["billing_codes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tagihan_tenant_id", "tagihan", ["tenant_id"])
    op.create_index("ix_tagihan_user_id", "tagihan", ["user_id"])
    op.create_index("ix_tagihan_status", "tagihan", ["status"])
    op.create_index("ix_tagihan_billing_id", "tagihan", ["billing_id"])

    op.create_table(
        "angsuran",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("billing_id", sa.Integer(), nullable=False),
        sa.Column("n_cicilan", sa.Integer(), nullable=False),
        sa.Column("status", sa.Enum("berjalan", "lunas", "batal", name="angsuran_status"), nullable=False, server_default="berjalan"),
        sa.ForeignKeyConstraint(["billing_id"], ["billing_codes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("billing_id"),
    )
    op.create_index("ix_angsuran_tenant_id", "angsuran", ["tenant_id"])
    op.create_index("ix_angsuran_user_id", "angsuran", ["user_id"])
    op.create_index("ix_angsuran_status", "angsuran", ["status"])

    op.create_table(
        "cicilan",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("angsuran_id", sa.Integer(), nullable=False),
        sa.Column("ke", sa.Integer(), nullable=False),
        sa.Column("jatuh_tempo", sa.Date(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Enum("belum", "lunas", name="cicilan_status"), nullable=False, server_default="belum"),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kanal", sa.String(length=40), nullable=True),
        sa.ForeignKeyConstraint(["angsuran_id"], ["angsuran.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cicilan_angsuran_id", "cicilan", ["angsuran_id"])
    op.create_index("ix_cicilan_status", "cicilan", ["status"])

    op.add_column("tax_returns", sa.Column("payment_ref", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("tax_returns", "payment_ref")

    op.drop_index("ix_cicilan_status", table_name="cicilan")
    op.drop_index("ix_cicilan_angsuran_id", table_name="cicilan")
    op.drop_table("cicilan")

    op.drop_index("ix_angsuran_status", table_name="angsuran")
    op.drop_index("ix_angsuran_user_id", table_name="angsuran")
    op.drop_index("ix_angsuran_tenant_id", table_name="angsuran")
    op.drop_table("angsuran")

    op.drop_index("ix_tagihan_billing_id", table_name="tagihan")
    op.drop_index("ix_tagihan_status", table_name="tagihan")
    op.drop_index("ix_tagihan_user_id", table_name="tagihan")
    op.drop_index("ix_tagihan_tenant_id", table_name="tagihan")
    op.drop_table("tagihan")

    op.drop_index("ix_pbk_requests_status", table_name="pbk_requests")
    op.drop_index("ix_pbk_requests_user_id", table_name="pbk_requests")
    op.drop_index("ix_pbk_requests_tenant_id", table_name="pbk_requests")
    op.drop_table("pbk_requests")

    op.drop_index("ix_deposit_mutations_account_id", table_name="deposit_mutations")
    op.drop_table("deposit_mutations")

    op.drop_index("ix_deposit_accounts_tenant_id", table_name="deposit_accounts")
    op.drop_index("ix_deposit_accounts_user_id", table_name="deposit_accounts")
    op.drop_table("deposit_accounts")

    op.execute("DROP TYPE IF EXISTS cicilan_status")
    op.execute("DROP TYPE IF EXISTS angsuran_status")
    op.execute("DROP TYPE IF EXISTS tagihan_status")
    op.execute("DROP TYPE IF EXISTS tagihan_jenis")
    op.execute("DROP TYPE IF EXISTS pbk_status")
    op.execute("DROP TYPE IF EXISTS deposit_mutation_kind")
