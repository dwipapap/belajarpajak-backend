"""Unit and integration tests for rule-based auto-grading (app/services/auto_grade.py)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlmodel import Session

from app.db import engine
from app.models.billing import BillingCode
from app.models.enums import (
    AssignmentDocType,
    BillingStatus,
    BuyerIdentityType,
    SlipStatus,
    SlipTaxFacility,
    SlipTaxNature,
    SlipType,
    TaxInputCreditStatus,
    TaxInvoiceKind,
    TaxInvoiceLineType,
    TaxInvoiceStatus,
    TaxReturnStatus,
    TaxReturnType,
)
from app.models.slip import WithholdingSlip
from app.models.tax_input_invoice import TaxInputInvoice, TaxInputLine
from app.models.tax_invoice import TaxInvoice, TaxInvoiceLine
from app.models.tax_return import TaxReturn
from app.services.auto_grade import auto_grade_document


def _checks_by_code(checks: list[dict]) -> dict[str, dict]:
    return {c["code"]: c for c in checks}


def test_auto_grade_bppu_valid_and_draft_and_wrong_math():
    valid_slip = WithholdingSlip(
        id=9001,
        tenant_id=1,
        siswa_id=1,
        created_by_id=1,
        slip_type=SlipType.bppu,
        status=SlipStatus.issued,
        withholding_number="SIM-BPPU-202505-009001",
        electronic_signature_status="signed",
        signer_name="Siswa Simulasi",
        issued_at=datetime(2025, 5, 15, 10, 0, tzinfo=UTC),
        tax_month=5,
        tax_year=2025,
        document_date=date(2025, 5, 15),
        recipient_identity_number="0123456789012345",
        recipient_name="PT Konsultan Nusantara",
        tax_object_code="22-100-07",
        kap_kjs="411122-100",
        tax_nature=SlipTaxNature.non_final,
        tax_facility=SlipTaxFacility.none,
        gross_income=10_000_000,
        dpp_rate_basis_points=10_000,
        dpp=10_000_000,
        rate_basis_points=25,  # 0.25% (22-100-07)
        income_tax=25_000,
    )
    with Session(engine) as session:
        score, checks = auto_grade_document(session, AssignmentDocType.bppu, valid_slip)
    assert score == 100
    assert all(c["passed"] for c in checks)

    # Draft slip fails doc_status and signature
    draft_slip = valid_slip.model_copy(
        update={
            "status": SlipStatus.draft,
            "withholding_number": None,
            "electronic_signature_status": "not_signed",
            "signer_name": None,
        }
    )
    with Session(engine) as session:
        score_draft, checks_draft = auto_grade_document(
            session, AssignmentDocType.bppu, draft_slip
        )
    by_code = _checks_by_code(checks_draft)
    assert not by_code["doc_status"]["passed"]
    assert not by_code["signature"]["passed"]
    assert score_draft == 70

    # Tampered rate & PPh math + out-of-period document_date
    wrong_slip = valid_slip.model_copy(
        update={
            "rate_basis_points": 200,  # 2% instead of 0.25%
            "income_tax": 999_999,
            "document_date": date(2025, 6, 1),  # month 6 != tax_month 5
        }
    )
    with Session(engine) as session:
        score_wrong, checks_wrong = auto_grade_document(
            session, AssignmentDocType.bppu, wrong_slip
        )
    by_code_w = _checks_by_code(checks_wrong)
    assert not by_code_w["period_and_date"]["passed"]
    assert not by_code_w["tax_object_and_rate"]["passed"]
    assert not by_code_w["tax_calculation"]["passed"]
    assert score_wrong < 60


@pytest.mark.parametrize(
    "slip_type,code,kap,rate_bp,ident,ptkp,ter_cat,dpp_bp,dpp,pph",
    [
        (
            SlipType.bp21,
            "21-100-04",
            "411121-100",
            0,
            "3201234567890001",
            "TK/0",
            "A",
            10_000,
            8_000_000,
            120_000,  # Official PMK 168/2023 TER A for 8jt = 1.50% -> 120,000
        ),
        (
            SlipType.bp26,
            "27-100-99",
            "411127-100",
            2000,
            "PASS-SG-9981",
            None,
            None,
            10_000,
            10_000_000,
            2_000_000,
        ),
        (
            SlipType.bpmp,
            "21-100-01",
            "411121-100",
            150,
            "3201234567890002",
            "TK/0",
            None,
            10_000,
            8_000_000,
            120_000,
        ),
        (
            SlipType.bpnr,
            "27-100-01",
            "411127-110",
            2000,
            "TIN-US-88123",
            None,
            None,
            5_000,  # 50% neto
            5_000_000,
            1_000_000,
        ),
        (
            SlipType.sp,
            "22-401-01",
            "411122-100",
            25,
            None,
            None,
            None,
            10_000,
            10_000_000,
            25_000,
        ),
        (
            SlipType.cy,
            "22-100-23",
            "411122-100",
            50,
            None,
            None,
            None,
            10_000,
            10_000_000,
            50_000,
        ),
    ],
)
def test_auto_grade_all_other_slip_types(
    slip_type, code, kap, rate_bp, ident, ptkp, ter_cat, dpp_bp, dpp, pph
):
    slip = WithholdingSlip(
        id=9100,
        tenant_id=1,
        siswa_id=1,
        created_by_id=1,
        slip_type=slip_type,
        status=SlipStatus.issued,
        withholding_number=f"SIM-{slip_type.value.upper()}-202505-009100",
        electronic_signature_status="signed",
        signer_name="Siswa Simulasi",
        issued_at=datetime(2025, 5, 15, 10, 0, tzinfo=UTC),
        tax_month=5,
        tax_year=2025,
        document_date=date(2025, 5, 15),
        recipient_identity_number=ident,
        recipient_name="Penerima Simulasi" if ident else None,
        ptkp_status=ptkp,
        ter_category=ter_cat,
        tax_object_code=code,
        kap_kjs=kap,
        tax_nature=(
            SlipTaxNature.final
            if slip_type in (SlipType.sp, SlipType.cy)
            else SlipTaxNature.non_final
        ),
        tax_facility=SlipTaxFacility.none,
        gross_income=10_000_000 if dpp_bp != 10_000 else dpp,
        dpp_rate_basis_points=dpp_bp,
        dpp=dpp,
        rate_basis_points=rate_bp,
        income_tax=pph,
    )
    with Session(engine) as session:
        score, checks = auto_grade_document(session, slip_type.value, slip)
    assert score == 100, f"{slip_type}: {checks}"


def test_auto_grade_faktur_keluaran_and_masukan():
    with Session(engine) as session:
        inv = TaxInvoice(
            tenant_id=1,
            siswa_id=1,
            created_by_id=1,
            status=TaxInvoiceStatus.issued,
            invoice_number="04002500000999001",
            electronic_signature_status="signed",
            signer_name="Siswa Simulasi",
            transaction_code="04",  # DPP Nilai Lain 11/12
            invoice_date=date(2025, 5, 10),
            invoice_kind=TaxInvoiceKind.normal,
            tax_month=5,
            tax_year=2025,
            buyer_identity_type=BuyerIdentityType.npwp,
            buyer_identity_number="0123456789012345",
            buyer_name="PT Pembeli",
            total_price=12_000_000,
            total_discount=0,
            total_dpp=12_000_000,
            total_dpp_other=11_000_000,
            total_ppn=1_320_000,
            total_ppnbm=0,
        )
        session.add(inv)
        session.flush()
        line = TaxInvoiceLine(
            invoice_id=inv.id,
            line_order=1,
            line_type=TaxInvoiceLineType.barang,
            item_name="Server Pembelajaran",
            unit="Unit",
            unit_price=12_000_000,
            quantity_milli=1000,
            total_price=12_000_000,
            discount=0,
            dpp=12_000_000,
            use_dpp_other=True,
            dpp_other=11_000_000,
            ppn_rate_basis_points=1200,
            ppn=1_320_000,
            ppnbm_rate_basis_points=0,
            ppnbm=0,
        )
        session.add(line)
        session.flush()

        score, checks = auto_grade_document(
            session, AssignmentDocType.faktur_keluaran, inv
        )
        assert score == 100

        # Tamper line DPP Nilai Lain
        line.dpp_other = 12_000_000
        session.add(line)
        session.flush()
        score_bad, checks_bad = auto_grade_document(
            session, AssignmentDocType.faktur_keluaran, inv
        )
        by_code = _checks_by_code(checks_bad)
        assert not by_code["line_calculations"]["passed"]
        assert not by_code["header_totals"]["passed"]
        assert score_bad == 50

        # Faktur Masukan
        masukan = TaxInputInvoice(
            tenant_id=1,
            siswa_id=1,
            created_by_id=1,
            status=TaxInvoiceStatus.issued,
            invoice_number="01002500000999002",
            credit_status=TaxInputCreditStatus.credited,
            transaction_code="01",
            invoice_date=date(2025, 5, 12),
            tax_month=5,
            tax_year=2025,
            seller_npwp="0123456789012345",
            seller_name="PT Penjual",
            buyer_identity_type=BuyerIdentityType.npwp,
            buyer_identity_number="9876543210987654",
            buyer_name="PT Pembeli",
            total_price=5_000_000,
            total_discount=0,
            total_dpp=5_000_000,
            total_dpp_other=0,
            total_ppn=600_000,
            total_ppnbm=0,
        )
        session.add(masukan)
        session.flush()
        mline = TaxInputLine(
            invoice_id=masukan.id,
            line_order=1,
            line_type=TaxInvoiceLineType.barang,
            item_name="Bahan Baku",
            unit="Paket",
            unit_price=5_000_000,
            quantity_milli=1000,
            total_price=5_000_000,
            discount=0,
            dpp=5_000_000,
            use_dpp_other=False,
            dpp_other=0,
            ppn_rate_basis_points=1200,
            ppn=600_000,
            ppnbm_rate_basis_points=0,
            ppnbm=0,
        )
        session.add(mline)
        session.flush()

        m_score, m_checks = auto_grade_document(
            session, AssignmentDocType.faktur_masukan, masukan
        )
        assert m_score == 100

        # Undecided credit_status fails credit_decision check
        masukan.credit_status = TaxInputCreditStatus.not_credited
        m_score_undecided, m_checks_undecided = auto_grade_document(
            session, AssignmentDocType.faktur_masukan, masukan
        )
        assert not _checks_by_code(m_checks_undecided)["credit_decision"]["passed"]
        assert m_score_undecided == 75
        session.rollback()


def test_auto_grade_spt_recompute_and_payment():
    with Session(engine) as session:
        # Use an isolated period (month=11, year=2024) for siswa_id=1
        slip = WithholdingSlip(
            tenant_id=1,
            siswa_id=1,
            created_by_id=1,
            slip_type=SlipType.bppu,
            status=SlipStatus.issued,
            withholding_number="SIM-BPPU-202411-990001",
            electronic_signature_status="signed",
            signer_name="Siswa Simulasi",
            tax_month=11,
            tax_year=2024,
            recipient_identity_number="0123456789012345",
            recipient_name="PT Rekanan",
            tax_object_code="22-100-07",
            kap_kjs="411122-100",
            tax_nature=SlipTaxNature.non_final,
            tax_facility=SlipTaxFacility.none,
            gross_income=10_000_000,
            dpp_rate_basis_points=10_000,
            dpp=10_000_000,
            rate_basis_points=25,
            income_tax=25_000,
        )
        session.add(slip)
        session.flush()

        tr = TaxReturn(
            tenant_id=1,
            siswa_id=1,
            created_by_id=1,
            return_type=TaxReturnType.pph_unifikasi,
            status=TaxReturnStatus.reported,
            tax_month=11,
            tax_year=2024,
            signer_name="Siswa Simulasi",
            submitted_at=datetime(2024, 12, 10, 9, 0, tzinfo=UTC),
            bpe_number="BPE-SIM-99001/2024",
            total_income_tax=25_000,
            amount_due=25_000,
        )
        session.add(tr)
        session.flush()

        # Without paid billing, payment_or_settlement fails
        score_unpaid, checks_unpaid = auto_grade_document(
            session, AssignmentDocType.spt_pph_unifikasi, tr
        )
        by_code = _checks_by_code(checks_unpaid)
        assert by_code["recomputed_totals"]["passed"]
        assert not by_code["payment_or_settlement"]["passed"]
        assert score_unpaid == 80

        # Add paid billing
        billing = BillingCode(
            tenant_id=1,
            siswa_id=1,
            spt_id=tr.id,
            code="411122100112499",
            status=BillingStatus.paid,
            amount=25_000,
            kap_kjs="411122-100",
            masa_pajak="112024",
            ntpn="NTPN123456789012",
            paid_at=datetime(2024, 12, 10, 8, 55, tzinfo=UTC),
        )
        session.add(billing)
        session.flush()

        score_paid, checks_paid = auto_grade_document(
            session, AssignmentDocType.spt_pph_unifikasi, tr
        )
        assert score_paid == 100
        assert all(c["passed"] for c in checks_paid)
        session.rollback()
