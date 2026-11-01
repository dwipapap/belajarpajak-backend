"""add tenant berlaku_dari (period start)

Revision ID: e7a1b2c3d4f5
Revises: 9f8e7d6c5b4a
Create Date: 2026-11-01 00:00:00.000000

- tenants: berlaku_dari DATE NULL (start of the validity period; NULL for
  legacy rows). Creation fills it with today; PATCH extend keeps it unless
  the period already expired, in which case a new period starts from today.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7a1b2c3d4f5"
down_revision: str | Sequence[str] | None = "9f8e7d6c5b4a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("berlaku_dari", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "berlaku_dari")
