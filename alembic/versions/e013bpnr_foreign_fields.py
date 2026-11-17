"""BPNR foreign-recipient fields: country, birth data, passport, KITAS, payment method, SP2D.

Revision ID: e013bpnrforeign
Revises: e012leburbp23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e013bpnrforeign"
down_revision: str | None = "e012leburbp23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS: list[tuple[str, sa.types.TypeEngine]] = [
    ("recipient_country", sa.String(length=100)),
    ("recipient_birth_date", sa.Date()),
    ("recipient_birth_place", sa.String(length=100)),
    ("recipient_passport_number", sa.String(length=40)),
    ("recipient_kitas_number", sa.String(length=40)),
    ("payment_method", sa.String(length=40)),
    ("sp2d_number", sa.String(length=60)),
]


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        op.add_column("withholding_slips", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.drop_column("withholding_slips", name)
