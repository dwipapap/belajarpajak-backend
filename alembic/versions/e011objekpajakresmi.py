"""011: align kode objek pajak with the official catalog

Revision ID: e011objekpajakresmi
Revises: e010sliprecipient
Create Date: 2026-11-14

* Adds ``treaty`` (SKD, fasilitas 7) and ``lainnya`` (Fasilitas Lainnya, 8) to the
  ``slip_tax_facility`` enum.
* Remaps existing slips from the old hand-made kode objek to the closest
  official code BY MEANING (also sets tax_object_name and kap_kjs from the
  catalog). One CASE update per slip type, so chained remaps cannot double-apply.
  Rate and income_tax of existing slips are left untouched.

Old -> new (see app/services/objek_pajak_remap.py):

bp23: 24-100-01 Dividen -> 24-101-01 | 24-101-01 Bunga -> 24-102-01 |
      24-102-01 Sewa harta -> 24-100-02 | 24-103-01 Royalti -> 24-103-01 |
      24-104-01 Jasa konsultan -> 24-104-03
bppu: same as bp23, plus 28-100-01 Jasa konstruksi -> 28-409-10
sp:   24-100-01 Dividen -> 28-419-01 | 24-102-01 Sewa -> 28-403-02
cy:   24-100-01 Dividen -> 28-419-01 | 24-102-01 Sewa -> 24-105-01 (closest) |
      27-100-01 Dividen WPLN -> 27-102-03 (only Pasal 26 object in CY)
bp21: 21-100-01 -> 21-100-35 | 21-100-02 -> 21-100-12 | 21-100-09 -> 21-100-07 |
      21-401-01 -> 21-100-35 | 21-401-02 -> 21-100-24 | 21-499-99 -> 21-100-20
bp26: 27-100-01..05 -> 27-100-99
bpnr: 27-100-01 -> 27-101-01 | 27-100-04 -> 27-104-01
bpmp: 21-100-01 -> 21-100-01 | 21-100-02, 21-100-09, 21-401-01, 21-401-02,
      21-499-99 -> 21-100-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.services.objek_pajak_remap import REMAP, remap_rows

# revision identifiers, used by Alembic.
revision: str = "e011objekpajakresmi"
down_revision: str | None = "e010sliprecipient"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE slip_tax_facility ADD VALUE IF NOT EXISTS 'treaty'")
    op.execute("ALTER TYPE slip_tax_facility ADD VALUE IF NOT EXISTS 'lainnya'")

    bind = op.get_bind()
    for slip_type in REMAP:
        rows = remap_rows(slip_type)
        if not rows:
            continue
        params: dict[str, str] = {"slip_type": slip_type}
        code_case, name_case, kap_case, olds = [], [], [], []
        for i, (old, new, name, kap) in enumerate(rows):
            params.update({f"o{i}": old, f"c{i}": new, f"n{i}": name, f"k{i}": kap})
            code_case.append(f"WHEN :o{i} THEN :c{i}")
            name_case.append(f"WHEN :o{i} THEN :n{i}")
            kap_case.append(f"WHEN :o{i} THEN :k{i}")
            olds.append(f":o{i}")
        bind.execute(
            sa.text(
                "UPDATE withholding_slips SET "
                f"tax_object_code = CASE tax_object_code {' '.join(code_case)} END, "
                f"tax_object_name = CASE tax_object_code {' '.join(name_case)} END, "
                f"kap_kjs = CASE tax_object_code {' '.join(kap_case)} END "
                f"WHERE slip_type = CAST(:slip_type AS slip_type) "
                f"AND tax_object_code IN ({', '.join(olds)})"
            ),
            params,
        )


def downgrade() -> None:
    # Data remap is lossy (several old codes collapse into one) and PostgreSQL
    # cannot drop enum values; nothing is reverted.
    pass
