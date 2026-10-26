"""merge bppu/bpmp and unifikasi heads

Revision ID: 9f8e7d6c5b4a
Revises: b3c4d5e6f7a8, d0e1f2a3b4c5
Create Date: 2026-10-26 00:00:00.000000

Merge of the local plans 003-006 head (b3c4d5e6f7a8: bppu + bpmp +
pph_21_26) and the remote eBupot Fase 1-5 head (d0e1f2a3b4c5: TER fields,
signer, pph21_26, unifikasi types, billing uraian). No DDL — both branches
are additive and enum inserts are IF NOT EXISTS idempotent.
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "9f8e7d6c5b4a"
down_revision: str | Sequence[str] | None = ("b3c4d5e6f7a8", "d0e1f2a3b4c5")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
