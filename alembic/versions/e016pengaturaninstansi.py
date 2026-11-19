"""Add allowed_modules, enabled_modules, and settings columns to tenants.

Revision ID: e016pengaturaninstansi
Revises: e015tugasassignments
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e016pengaturaninstansi"
down_revision: str | None = "e015tugasassignments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "allowed_modules",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "enabled_modules",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "settings")
    op.drop_column("tenants", "enabled_modules")
    op.drop_column("tenants", "allowed_modules")
