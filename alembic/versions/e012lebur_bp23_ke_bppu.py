"""012: merge BP23 slips into BPPU

Revision ID: e012leburbp23
Revises: e011objekpajakresmi
Create Date: 2026-11-17

The reference app has no separate BP23 menu; PPh 23 slips are BPPU slips with
a Pasal 23 tax object (24-xxx-xx). Existing ``bp23`` rows become ``bppu``:

* ``withholding_slips.slip_type`` bp23 -> bppu (the ``bp23`` enum value stays
  in ``slip_type``; PostgreSQL cannot drop enum values cheaply).
* ``withholding_slips.withholding_number`` prefix ``SIM-BP23-`` -> ``SIM-BPPU-``
  (the slip id inside the number keeps it unique).
* ``audit_logs.entity`` bp23 -> bppu so history links keep resolving.

SPT lines snapshot only pasal/KJS (no slip type), so nothing to change there.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e012leburbp23"
down_revision: str | None = "e011objekpajakresmi"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE withholding_slips SET "
            "slip_type = CAST('bppu' AS slip_type), "
            "withholding_number = CASE WHEN withholding_number LIKE 'SIM-BP23-%' "
            "THEN 'SIM-BPPU-' || substr(withholding_number, 10) "
            "ELSE withholding_number END "
            "WHERE slip_type = CAST('bp23' AS slip_type)"
        )
    )
    op.execute(sa.text("UPDATE audit_logs SET entity = 'bppu' WHERE entity = 'bp23'"))


def downgrade() -> None:
    # Not reversible: once merged, BP23 rows are indistinguishable from other
    # BPPU rows (and the BP23 module no longer exists), so nothing is reverted.
    pass
