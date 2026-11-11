"""009: SPT lb_treatment + cancelled billing status

Revision ID: c009sptlampiran
Revises: b10e5c010bpe
Create Date: 2026-11-01

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel  # noqa: F401

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c009sptlampiran'
down_revision: str | None = 'b10e5c010bpe'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE billing_status ADD VALUE IF NOT EXISTS 'cancelled'")
    sa.Enum("kompensasi", "restitusi", name="lb_treatment").create(
        op.get_bind(), checkfirst=True
    )
    op.add_column(
        'tax_returns',
        sa.Column('lb_treatment', sa.Enum("kompensasi", "restitusi", name="lb_treatment"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('tax_returns', 'lb_treatment')
    sa.Enum("kompensasi", "restitusi", name="lb_treatment").drop(
        op.get_bind(), checkfirst=True
    )
