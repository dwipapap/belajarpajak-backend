"""Data migration e012: bp23 slips become bppu (rolled back after the check)."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlmodel import Session

from app.db import engine
from app.models.enums import SlipStatus, SlipTaxFacility, SlipTaxNature, SlipType
from app.models.slip import WithholdingSlip

_PATH = Path(__file__).resolve().parent.parent / "alembic/versions/e012lebur_bp23_ke_bppu.py"


def test_upgrade_converts_bp23_rows():
    spec = importlib.util.spec_from_file_location("e012", _PATH)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            ids = conn.execute(
                text(
                    "SELECT t.id, u.id FROM tenants t JOIN users u ON u.tenant_id = t.id "
                    "WHERE u.role = 'siswa' LIMIT 1"
                )
            ).one()
            session = Session(bind=conn)
            slip = WithholdingSlip(
                slip_type=SlipType.bp23,
                tenant_id=ids[0],
                siswa_id=ids[1],
                created_by_id=ids[1],
                status=SlipStatus.issued,
                withholding_number="SIM-BP23-202601-999999",
                tax_month=1,
                tax_year=2026,
                tax_object_code="24-101-01",
                tax_nature=SlipTaxNature.non_final,
                tax_facility=SlipTaxFacility.none,
                gross_income=1,
                dpp=1,
                dpp_rate_basis_points=10_000,
                rate_basis_points=1_500,
                income_tax=0,
            )
            session.add(slip)
            session.flush()
            slip_id = slip.id
            with Operations.context(MigrationContext.configure(conn)):
                mig.upgrade()
            got = conn.execute(
                text(
                    "SELECT slip_type::text, withholding_number FROM withholding_slips WHERE id=:i"
                ),
                {"i": slip_id},
            ).one()
            assert got == ("bppu", "SIM-BPPU-202601-999999")
        finally:
            trans.rollback()
