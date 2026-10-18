"""add unifikasi slip types bppu/bpnr/sp/cy

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-10-18 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c0d1e2f3a4b5"
down_revision: str | None = "b9c0d1e2f3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for value in ("bppu", "bpnr", "sp", "cy"):
        op.execute(f"ALTER TYPE slip_type ADD VALUE '{value}'")


def downgrade() -> None:
    pass
