"""account foundation: username, quotas, tenant validity, audit logs

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-10-04 15:00:00.000000

- users: username (siswa login identifier, globally unique), email becomes
  globally unique, must_change_password flag
- tenants: contact_wa, berlaku_sampai, max_guru/max_siswa/max_kelas,
  deactivated_reason; tenant_type gains the 'pt' value
- classes: archived flag
- audit_logs: new table
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel  # noqa: F401
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # New enum value must be added before it is used by any row.
    op.execute("ALTER TYPE tenant_type ADD VALUE IF NOT EXISTS 'pt'")

    # --- users ---
    op.add_column(
        "users",
        sa.Column("username", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True),
    )
    op.create_index("uq_users_username", "users", ["username"], unique=True)
    op.add_column(
        "users", sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.text("false"))
    )
    # Global email uniqueness replaces the per-tenant composite constraint +
    # the superadmin partial index.
    op.drop_index("uq_users_superadmin_email", table_name="users")
    op.drop_constraint("uq_users_tenant_email", "users", type_="unique")
    op.create_index("uq_users_email", "users", ["email"], unique=True)

    # Backfill login usernames for pre-existing siswa accounts.
    op.execute(
        "UPDATE users SET username = 'NIS' || lpad(id::text, 8, '0') "
        "WHERE role = 'siswa' AND username IS NULL"
    )

    # --- tenants ---
    op.add_column("tenants", sa.Column("contact_wa", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=True))
    op.add_column("tenants", sa.Column("berlaku_sampai", sa.Date(), nullable=True))
    op.add_column("tenants", sa.Column("max_guru", sa.Integer(), nullable=True))
    op.add_column("tenants", sa.Column("max_siswa", sa.Integer(), nullable=True))
    op.add_column("tenants", sa.Column("max_kelas", sa.Integer(), nullable=True))
    op.add_column("tenants", sa.Column("deactivated_reason", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True))

    # --- classes ---
    op.add_column("classes", sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")))

    # --- audit logs ---
    op.create_table(
        "audit_logs",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("actor_email", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("actor_role", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
        sa.Column("action", sqlmodel.sql.sqltypes.AutoString(length=60), nullable=False),
        sa.Column("entity", sqlmodel.sql.sqltypes.AutoString(length=40), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("summary", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_logs_tenant_id"), "audit_logs", ["tenant_id"])
    op.create_index(op.f("ix_audit_logs_action"), "audit_logs", ["action"])
    op.create_index(op.f("ix_audit_logs_entity"), "audit_logs", ["entity"])


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_logs_entity"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_action"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_tenant_id"), table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_column("classes", "archived")

    op.drop_column("tenants", "deactivated_reason")
    op.drop_column("tenants", "max_kelas")
    op.drop_column("tenants", "max_siswa")
    op.drop_column("tenants", "max_guru")
    op.drop_column("tenants", "berlaku_sampai")
    op.drop_column("tenants", "contact_wa")

    op.drop_index("uq_users_email", table_name="users")
    op.create_index(
        "uq_users_superadmin_email",
        "users",
        ["email"],
        unique=True,
        postgresql_where=sa.text("tenant_id IS NULL"),
    )
    op.create_unique_constraint("uq_users_tenant_email", "users", ["tenant_id", "email"])
    op.drop_column("users", "must_change_password")
    op.drop_index("uq_users_username", table_name="users")
    op.drop_column("users", "username")

    # Enum value removal is not supported by Postgres — documented no-op.
