"""Simulated e-Bupot withholding slip routes — one factory for all slip types.

BP21, BP23 and BP26 share the same form shape with different data values, so all
mount the identical endpoint set; ``slip_type`` scopes every query. Adding a new
bupot type is one enum value + one ``make_slip_router`` call.

Learning scope (Fase 1+2): numbers are clearly simulated (``SIM-`` prefix), identity
formats (NPWP 16 / NITKU 22) and kode objek ↔ tarif ↔ KAP-KJS are validated
educationally, SKB requires a document number and treaty requires SKD data.
Fase 2 adds SIM-TER (A/B/C, Jan–Nov) vs progresif Desember for BP21.
"""

from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status
from pydantic import ValidationError
from sqlmodel import Session, func, select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.core.money import apply_basis_points, basis_points_to_percent, percent_to_basis_points
from app.core.scope import resolve_document_scope
from app.models.enums import (
    Role,
    SlipSptFlag,
    SlipStatus,
    SlipTaxFacility,
    SlipTaxNature,
    SlipType,
)
from app.models.school_class import SchoolClass
from app.models.slip import WithholdingSlip
from app.models.tarif_pajak import TarifProgresifPasal17, TierPtkp
from app.models.user import User
from app.schemas.slip import (
    SlipBulkIssue,
    SlipBulkIssueResult,
    SlipCancel,
    SlipCreate,
    SlipImportResult,
    SlipImportRowResult,
    SlipInvalidate,
    SlipIssue,
    SlipListResponse,
    SlipRead,
    SlipReview,
    SlipSptFlagUpdate,
    SlipSummary,
    SlipUpdate,
)
from app.services.audit import log_action

MAX_IMPORT_BYTES = 1_000_000
MAX_IMPORT_ROWS = 500
MAX_EXPORT_ROWS = 5_000

#: Prefix that marks every generated number as simulated learning data.
SIM_NUMBER_PREFIX = "SIM-"

#: Learning matrix: kode objek → (rate_percent, kap_kjs). Only the codes students
#: must recognise for SMK/kuliah practice; Coretax has dozens more.
TAX_OBJECT_MATRIX: dict[str, dict[str, tuple[float, str]]] = {
    SlipType.bp21.value: {
        "21-100-01": (5.0, "411121-100"),
        "21-100-02": (5.0, "411121-100"),
        "21-100-09": (5.0, "411121-100"),
        "21-401-01": (5.0, "411121-100"),
        "21-401-02": (5.0, "411121-100"),
        "21-499-99": (5.0, "411121-100"),
    },
    SlipType.bp23.value: {
        "24-100-01": (15.0, "411124-100"),
        "24-101-01": (15.0, "411124-102"),
        "24-103-01": (15.0, "411124-103"),
        "24-102-01": (2.0, "411124-104"),
        "24-104-01": (2.0, "411124-104"),
    },
    SlipType.bp26.value: {
        "27-100-01": (20.0, "411127-100"),
        "27-100-02": (20.0, "411127-101"),
        "27-100-03": (20.0, "411127-102"),
        "27-100-04": (20.0, "411127-104"),
        "27-100-05": (20.0, "411127-100"),
    },
    # Unifikasi minimal untuk belajar (subset PPh 23/26 + setor-sendiri/digunggung).
    SlipType.bppu.value: {
        "24-100-01": (15.0, "411124-100"),
        "24-102-01": (2.0, "411124-104"),
        "24-104-01": (2.0, "411124-104"),
    },
    SlipType.bpnr.value: {
        "27-100-01": (20.0, "411127-100"),
        "27-100-04": (20.0, "411127-104"),
    },
    SlipType.sp.value: {
        "24-100-01": (15.0, "411124-100"),
        "24-102-01": (2.0, "411124-104"),
    },
    SlipType.cy.value: {
        "24-100-01": (15.0, "411124-100"),
        "27-100-01": (20.0, "411127-100"),
    },
}

#: SIM-TER (disederhanakan untuk belajar, bukan tarif resmi).
#: Kategori A: TK/0, TK/1, K/0 · B: TK/2, TK/3, K/1, K/2 · C: K/3.
#: Tiap entri = (batas_atas_bruto, tarif_basis_points). Batas None = tak terbatas.
SIM_TER_RATES: dict[str, list[tuple[int | None, int]]] = {
    "A": [(5_000_000, 0), (10_000_000, 100), (20_000_000, 200), (None, 300)],
    "B": [(5_000_000, 0), (10_000_000, 50), (20_000_000, 150), (None, 250)],
    "C": [(5_000_000, 0), (10_000_000, 0), (20_000_000, 100), (None, 200)],
}

TER_CATEGORIES = ("A", "B", "C")


def _calculate_ter_tax(gross_income: int, ter_category: str | None) -> int | None:
    """SIM-TER monthly tax for BP21 Jan–Nov. Returns None for unknown category."""
    if not ter_category or ter_category not in SIM_TER_RATES:
        return None
    for upper, rate_bp in SIM_TER_RATES[ter_category]:
        if upper is None or gross_income <= upper:
            return apply_basis_points(gross_income, rate_bp)
    return None


def _validate_learning_fields(
    *,
    slip_type: SlipType,
    recipient_identity_number: str | None,
    withholder_npwp: str | None = None,
    withholder_nitku: str | None = None,
    recipient_nitku: str | None = None,
    document_nitku: str | None = None,
    tax_object_code: str | None = None,
    rate_percent: float | None = None,
    kap_kjs: str | None = None,
    tax_facility: SlipTaxFacility | None = None,
    document_number: str | None = None,
    negara_treaty: str | None = None,
    pasal_treaty: str | None = None,
    nomor_skd: str | None = None,
    tarif_treaty_basis_points: int | None = None,
    ter_category: str | None = None,
    tax_month: int | None = None,
) -> None:
    """Educational validation mirroring Coretax rules (simplified for SMK/kuliah).

    Raises 422 with Indonesian messages so students learn the format, not just
    presence. BP23 without NPWP (16 zeros) is allowed and handled by the x2 rate.
    BP26 allows foreign TIN/passport (non-digit identity).
    """

    def _fail(detail: str) -> None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail
        )

    if recipient_identity_number is not None:
        ident = recipient_identity_number.strip()
        if slip_type in (SlipType.bp21, SlipType.bp23, SlipType.bppu, SlipType.sp, SlipType.cy):
            if not ident.isdigit() or len(ident) != 16:
                _fail("Nomor identitas penerima harus 16 digit (NPWP 16 / NIK).")
        elif slip_type in (SlipType.bp26, SlipType.bpnr):
            if not ident:
                _fail("Nomor identitas penerima wajib diisi (TIN / paspor).")

    for label, value in (
        ("NPWP pemotong", withholder_npwp),
        ("NITKU pemotong", withholder_nitku),
        ("NITKU penerima", recipient_nitku),
        ("NITKU dokumen", document_nitku),
    ):
        if value is None or str(value).strip() == "":
            continue
        text = str(value).strip()
        if "NITKU" in label:
            if not text.isdigit() or len(text) != 22:
                _fail(f"{label} harus 22 digit bila diisi.")
        else:
            if not text.isdigit() or len(text) not in (15, 16):
                _fail(f"{label} harus 15 atau 16 digit bila diisi.")

    if tax_object_code is not None:
        matrix = TAX_OBJECT_MATRIX.get(slip_type.value, {})
        expected = matrix.get(tax_object_code)
        if expected is None:
            allowed = ", ".join(sorted(matrix))
            _fail(f"Kode objek {tax_object_code} belum dipelajari. Pilih: {allowed}.")
        exp_rate, exp_kap = expected
        if rate_percent is not None and abs(float(rate_percent) - exp_rate) > 1e-9:
            _fail(f"Tarif untuk {tax_object_code} harus {exp_rate:g}%.")
        if kap_kjs is not None and str(kap_kjs).strip() != "" and str(kap_kjs).strip() != exp_kap:
            _fail(f"KAP-KJS untuk {tax_object_code} harus {exp_kap}.")

    if tax_facility == SlipTaxFacility.skb and not (document_number or "").strip():
        _fail("Fasilitas SKB wajib mencantumkan nomor dokumen SKB.")

    treaty_used = tarif_treaty_basis_points is not None
    if treaty_used and slip_type in (SlipType.bp26, SlipType.bpnr):
        if not (negara_treaty or "").strip():
            _fail("Treaty wajib mencantumkan negara treaty.")
        if not (nomor_skd or "").strip():
            _fail("Treaty wajib mencantumkan nomor SKD.")

    if ter_category is not None and str(ter_category).strip() != "":
        if slip_type != SlipType.bp21:
            _fail("Kategori TER hanya untuk BP21.")
        cat = str(ter_category).strip().upper()
        if cat not in TER_CATEGORIES:
            _fail("Kategori TER harus A, B, atau C.")
        if tax_month == 12:
            _fail("TER hanya untuk Jan–Nov; Desember wajib progresif.")

#: Header aliases from the official converter (students may paste those names).
#: Maps alias → internal field. Only the few students actually meet in class.
HEADER_ALIASES = {
    "masa_pajak": "tax_month",
    "tahun_pajak": "tax_year",
    "nik": "recipient_identity_number",
    "npwp": "recipient_identity_number",
    "nomor_identitas": "recipient_identity_number",
    "nama": "recipient_name",
    "kode_objek": "tax_object_code",
    "kode_objek_pajak": "tax_object_code",
    "bruto": "gross_income",
    "penghasilan_bruto": "gross_income",
    "tarif": "rate_percent",
    "dpp": "dpp_percent",
    "nomor_dokumen": "document_number",
    "jenis_dokumen": "document_type",
}

_IMPORT_FIELDS = (
    "class_id",
    "tax_month",
    "tax_year",
    "withholder_npwp",
    "withholder_name",
    "withholder_nitku",
    "recipient_identity_number",
    "recipient_name",
    "recipient_address",
    "recipient_nitku",
    "ptkp_status",
    "ter_category",
    "tax_type",
    "tax_object_code",
    "tax_object_name",
    "income_type",
    "tax_nature",
    "tax_facility",
    "previous_gross_income",
    "gross_income",
    "dpp_percent",
    "rate_percent",
    "kap_kjs",
    "negara_treaty",
    "pasal_treaty",
    "nomor_skd",
    "tarif_treaty_basis_points",
    "document_type",
    "document_number",
    "document_date",
    "document_nitku",
)

_CSV_COLUMNS = (
    "Masa Pajak",
    "Nomor Pemotongan",
    "Status",
    "Status Tanda Tangan Elektronik",
    "Penandatangan",
    "Metode Hitung",
    "NITKU/Nomor Identitas Sub Unit Organisasi",
    "Jenis Pajak",
    "Kode Objek Pajak",
    "Nomor Identitas WP",
    "Nama",
    "Dasar Pengenaan Pajak (Rp)",
    "Pajak Penghasilan (Rp)",
    "Fasilitas Pajak",
)

# --- money math (basis points keep integer rupiah exact) ---------------------


def calculate_dpp(gross_income: int, dpp_rate_basis_points: int) -> int:
    return apply_basis_points(gross_income, dpp_rate_basis_points)


def calculate_income_tax(dpp: int, rate_basis_points: int, facility: SlipTaxFacility) -> int:
    if facility in (SlipTaxFacility.skb, SlipTaxFacility.rate_0):
        return 0
    return apply_basis_points(dpp, rate_basis_points)


def _tax_by_basis_points(amount: int, rate_basis_points: int) -> int:
    return apply_basis_points(amount, rate_basis_points)


def _calculate_bp21_progressive_tax(
    session: Session,
    *,
    gross_income: int,
    ptkp_status: str | None,
    tax_year: int,
) -> int | None:
    if not ptkp_status:
        return None

    ptkp = session.exec(
        select(TierPtkp.jumlah_ptkp).where(
            TierPtkp.status_kode == ptkp_status,
            TierPtkp.tahun_pajak == tax_year,
            TierPtkp.is_active.is_(True),
        )
    ).one_or_none()
    if ptkp is None:
        return None

    brackets = session.exec(
        select(TarifProgresifPasal17)
        .where(
            TarifProgresifPasal17.tahun_pajak == tax_year,
            TarifProgresifPasal17.is_active.is_(True),
        )
        .order_by(TarifProgresifPasal17.batas_bawah)
    ).all()
    if not brackets:
        return None

    pkp = max(0, gross_income * 12 - ptkp)
    annual_tax = 0
    for bracket in brackets:
        if pkp <= bracket.batas_bawah:
            continue
        upper = bracket.batas_atas if bracket.batas_atas is not None else pkp
        chunk = min(pkp, upper) - bracket.batas_bawah
        if chunk <= 0:
            continue
        annual_tax += _tax_by_basis_points(chunk, bracket.persentase_basis_points)
    return annual_tax // 12


def _resolve_income_tax(
    session: Session,
    *,
    slip_type: SlipType,
    tax_nature: SlipTaxNature,
    tax_facility: SlipTaxFacility,
    gross_income: int,
    dpp: int,
    rate_basis_points: int,
    ptkp_status: str | None,
    tax_year: int,
    negara_treaty: str | None,
    tarif_treaty_basis_points: int | None,
    recipient_identity_number: str | None,
    ter_category: str | None = None,
    tax_month: int | None = None,
) -> tuple[int, str]:
    if tax_facility in (SlipTaxFacility.skb, SlipTaxFacility.rate_0):
        return 0, "nol_skb"

    if (
        slip_type == SlipType.bp21
        and tax_nature == SlipTaxNature.non_final
        and ter_category
        and str(ter_category).strip().upper() in TER_CATEGORIES
        and tax_month is not None
        and 1 <= tax_month <= 11
    ):
        ter_tax = _calculate_ter_tax(gross_income, str(ter_category).strip().upper())
        if ter_tax is not None:
            return ter_tax, "ter"

    if slip_type == SlipType.bp21 and tax_nature == SlipTaxNature.non_final:
        progressive_tax = _calculate_bp21_progressive_tax(
            session,
            gross_income=gross_income,
            ptkp_status=ptkp_status,
            tax_year=tax_year,
        )
        if progressive_tax is not None:
            return progressive_tax, "progresif"

    if (
        slip_type in (SlipType.bp26, SlipType.bpnr)
        and tax_nature == SlipTaxNature.non_final
        and negara_treaty
        and tarif_treaty_basis_points is not None
    ):
        return _tax_by_basis_points(dpp, tarif_treaty_basis_points), "treaty"

    final_rate_basis_points = rate_basis_points
    if slip_type in (SlipType.bp23, SlipType.bppu, SlipType.sp, SlipType.cy) and (
        not recipient_identity_number or recipient_identity_number.startswith("0000000000000000")
    ):
        final_rate_basis_points *= 2
        return calculate_income_tax(dpp, final_rate_basis_points, tax_facility), "flat_x2"

    return calculate_income_tax(dpp, final_rate_basis_points, tax_facility), "flat"


# --- shared access / lifecycle helpers ---------------------------------------


def slip_to_read(slip: WithholdingSlip) -> SlipRead:
    """Map a slip row to its read schema; percent fields come from basis points."""
    return SlipRead.model_validate(
        {
            **slip.model_dump(),
            "dpp_percent": basis_points_to_percent(slip.dpp_rate_basis_points),
            "rate_percent": basis_points_to_percent(slip.rate_basis_points),
        }
    )


def issue_slip(
    slip: WithholdingSlip,
    label: str,
    *,
    signer_id: int | None = None,
    signer_name: str | None = None,
) -> None:
    slip.status = SlipStatus.issued
    slip.issued_at = datetime.now(UTC)
    slip.electronic_signature_status = "signed"
    slip.withholding_number = (
        f"{SIM_NUMBER_PREFIX}{label}-{slip.tax_year}{slip.tax_month:02d}-{slip.id:06d}"
    )
    if signer_id is not None:
        slip.signer_id = signer_id
    if signer_name:
        slip.signer_name = signer_name


def can_read_slip(session: Session, current_user: User, slip: WithholdingSlip) -> bool:
    if current_user.role == Role.superadmin:
        return True
    if current_user.role == Role.admin:
        return slip.tenant_id == current_user.tenant_id
    if current_user.role == Role.siswa:
        return slip.siswa_id == current_user.id
    if current_user.role == Role.guru and slip.class_id is not None:
        school_class = session.get(SchoolClass, slip.class_id)
        return school_class is not None and school_class.guru_id == current_user.id
    return False


def apply_access_filters(query, current_user: User):
    if current_user.role == Role.superadmin:
        return query
    if current_user.role == Role.admin:
        return query.where(WithholdingSlip.tenant_id == current_user.tenant_id)
    if current_user.role == Role.siswa:
        return query.where(WithholdingSlip.siswa_id == current_user.id)

    taught_class_ids = select(SchoolClass.id).where(SchoolClass.guru_id == current_user.id)
    return query.where(WithholdingSlip.class_id.in_(taught_class_ids))


# --- router factory -----------------------------------------------------------


def make_slip_router(slip_type: SlipType, label: str) -> APIRouter:
    """Full e-Bupot endpoint set for one slip type; all types share every feature."""
    prefix = slip_type.value  # "bp21" / "bp26"
    xml_tag = label.capitalize()  # "Bp21" / "Bp26"
    router = APIRouter(prefix=f"/{prefix}", tags=[prefix])

    _roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru, Role.siswa))
    #: Mutating document endpoints — the superadmin is deliberately excluded
    #: (read-only + account management only).
    _write_roles = Depends(require_roles(Role.admin, Role.guru, Role.siswa))
    _review_roles = Depends(require_roles(Role.admin, Role.guru))

    def _get_accessible_slip(
        slip_id: int, current_user: CurrentUser, session: SessionDep
    ) -> WithholdingSlip:
        slip = session.get(WithholdingSlip, slip_id)
        if (
            slip is None
            or slip.slip_type != slip_type
            or not can_read_slip(session, current_user, slip)
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} tidak ditemukan"
            )
        return slip

    def _period_conditions(
        status_filter: SlipStatus | None, tax_year: int | None, tax_month: int | None
    ) -> list:
        conditions = [WithholdingSlip.slip_type == slip_type]
        if status_filter is not None:
            conditions.append(WithholdingSlip.status == status_filter)
        if tax_year is not None:
            conditions.append(WithholdingSlip.tax_year == tax_year)
        if tax_month is not None:
            conditions.append(WithholdingSlip.tax_month == tax_month)
        return conditions

    def _build_slip(
        data: SlipCreate, current_user: CurrentUser, session: SessionDep
    ) -> WithholdingSlip:
        """Validate scope and construct an unsaved draft slip from a create payload."""
        _validate_learning_fields(
            slip_type=slip_type,
            recipient_identity_number=data.recipient_identity_number,
            withholder_npwp=data.withholder_npwp,
            withholder_nitku=data.withholder_nitku,
            recipient_nitku=data.recipient_nitku,
            document_nitku=data.document_nitku,
            tax_object_code=data.tax_object_code,
            rate_percent=data.rate_percent,
            kap_kjs=data.kap_kjs,
            tax_facility=data.tax_facility,
            document_number=data.document_number,
            negara_treaty=data.negara_treaty,
            pasal_treaty=data.pasal_treaty,
            nomor_skd=data.nomor_skd,
            tarif_treaty_basis_points=data.tarif_treaty_basis_points,
            ter_category=data.ter_category,
            tax_month=data.tax_month,
        )
        tenant_id, class_id, siswa_id = resolve_document_scope(
            session=session,
            current_user=current_user,
            class_id=data.class_id,
            siswa_id=data.siswa_id,
            label=label,
        )
        rate_basis_points = percent_to_basis_points(data.rate_percent)
        dpp_rate_basis_points = percent_to_basis_points(data.dpp_percent)
        dpp = calculate_dpp(data.gross_income, dpp_rate_basis_points)
        income_tax, calculation_method = _resolve_income_tax(
            session,
            slip_type=slip_type,
            tax_nature=data.tax_nature,
            tax_facility=data.tax_facility,
            gross_income=data.gross_income,
            dpp=dpp,
            rate_basis_points=rate_basis_points,
            ptkp_status=data.ptkp_status,
            tax_year=data.tax_year,
            negara_treaty=data.negara_treaty,
            tarif_treaty_basis_points=data.tarif_treaty_basis_points,
            recipient_identity_number=data.recipient_identity_number,
            ter_category=data.ter_category,
            tax_month=data.tax_month,
        )
        payload = data.model_dump(exclude={"class_id", "siswa_id", "dpp_percent", "rate_percent"})
        return WithholdingSlip(
            **payload,
            slip_type=slip_type,
            tenant_id=tenant_id,
            class_id=class_id,
            siswa_id=siswa_id,
            created_by_id=current_user.id,
            dpp=dpp,
            dpp_rate_basis_points=dpp_rate_basis_points,
            rate_basis_points=rate_basis_points,
            income_tax=income_tax,
            calculation_method=calculation_method,
        )

    def _export_slips(current_user: CurrentUser, session: SessionDep, conditions: list):
        total = session.exec(
            apply_access_filters(
                select(func.count()).select_from(WithholdingSlip).where(*conditions),
                current_user,
            )
        ).one()
        if total > MAX_EXPORT_ROWS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Terlalu banyak data untuk ekspor. Persempit filter masa pajak atau status.",
            )
        query = apply_access_filters(
            select(WithholdingSlip)
            .where(*conditions)
            .order_by(WithholdingSlip.id)
            .limit(MAX_EXPORT_ROWS),
            current_user,
        )
        return session.exec(query).all()

    @router.get("", response_model=SlipListResponse, dependencies=[_roles])
    def list_slips(
        current_user: CurrentUser,
        session: SessionDep,
        status_filter: Annotated[SlipStatus | None, Query(alias="status")] = None,
        spt_flag: SlipSptFlag | None = None,
        tenant_id: int | None = None,
        class_id: int | None = None,
        siswa_id: int | None = None,
        tax_year: Annotated[int | None, Query(ge=2020, le=2100)] = None,
        tax_month: Annotated[int | None, Query(ge=1, le=12)] = None,
        tax_facility: SlipTaxFacility | None = None,
        page: Annotated[int, Query(ge=1)] = 1,
        size: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> SlipListResponse:
        conditions = _period_conditions(status_filter, tax_year, tax_month)
        if spt_flag is not None:
            conditions.append(WithholdingSlip.spt_flag == spt_flag)
        if current_user.role == Role.superadmin and tenant_id is not None:
            conditions.append(WithholdingSlip.tenant_id == tenant_id)
        if class_id is not None:
            conditions.append(WithholdingSlip.class_id == class_id)
        if siswa_id is not None and current_user.role != Role.siswa:
            conditions.append(WithholdingSlip.siswa_id == siswa_id)
        if tax_facility is not None:
            conditions.append(WithholdingSlip.tax_facility == tax_facility)

        base_count = apply_access_filters(
            select(func.count()).select_from(WithholdingSlip).where(*conditions),
            current_user,
        )
        base_items = apply_access_filters(
            select(WithholdingSlip)
            .where(*conditions)
            .order_by(WithholdingSlip.id.desc())
            .offset((page - 1) * size)
            .limit(size),
            current_user,
        )
        total = session.exec(base_count).one()
        items = session.exec(base_items).all()
        return SlipListResponse(
            items=[slip_to_read(item) for item in items],
            total=total,
            page=page,
            size=size,
        )

    @router.get("/summary", response_model=SlipSummary, dependencies=[_roles])
    def slip_summary(current_user: CurrentUser, session: SessionDep) -> SlipSummary:
        query = apply_access_filters(
            select(WithholdingSlip.status, func.count())
            .select_from(WithholdingSlip)
            .where(WithholdingSlip.slip_type == slip_type)
            .group_by(WithholdingSlip.status),
            current_user,
        )
        counts = dict(session.exec(query).all())
        return SlipSummary(
            draft=counts.get(SlipStatus.draft, 0),
            issued=counts.get(SlipStatus.issued, 0),
            invalid=counts.get(SlipStatus.invalid, 0),
            total=sum(counts.values()),
        )

    @router.get("/import-template", dependencies=[_roles])
    def import_template() -> Response:
        """Download the XML import format expected by POST /{type}/import-xml."""
        example = ET.Element(f"{xml_tag}List")
        row = ET.SubElement(example, xml_tag)
        if slip_type == SlipType.bp26:
            sample_tax_type = "PPh 26"
            sample_code = "27-100-01"
            sample_name = "Dividen yang diterima WPLN"
            sample_nature = "final"
            sample_rate = "20"
            sample_kap = "411127-100"
        elif slip_type == SlipType.bp23:
            sample_tax_type = "PPh 23"
            sample_code = "24-100-01"
            sample_name = "Dividen"
            sample_nature = "non_final"
            sample_rate = "15"
            sample_kap = "411124-100"
        elif slip_type in (SlipType.bppu, SlipType.sp, SlipType.cy):
            sample_tax_type = "PPh 23"
            sample_code = "24-100-01"
            sample_name = "Dividen (Unifikasi simulasi)"
            sample_nature = "non_final"
            sample_rate = "15"
            sample_kap = "411124-100"
        elif slip_type == SlipType.bpnr:
            sample_tax_type = "PPh 26"
            sample_code = "27-100-01"
            sample_name = "Dividen WPLN (BPNR simulasi)"
            sample_nature = "final"
            sample_rate = "20"
            sample_kap = "411127-100"
        else:
            sample_tax_type = "PPh 21"
            sample_code = "21-100-09"
            sample_name = "Honorarium tenaga ahli"
            sample_nature = "non_final"
            sample_rate = "5"
            sample_kap = "411121-100"
        samples = {
            "class_id": "",
            "tax_month": "1",
            "tax_year": "2026",
            "recipient_identity_number": "1234567890123456",
            "recipient_name": "JOHN DOE",
            "tax_type": sample_tax_type,
            "tax_object_code": sample_code,
            "tax_object_name": sample_name,
            "tax_nature": sample_nature,
            "tax_facility": "none",
            "gross_income": "10000000",
            "dpp_percent": "100",
            "rate_percent": sample_rate,
            "kap_kjs": sample_kap,
            "negara_treaty": "",
            "pasal_treaty": "",
            "nomor_skd": "",
            "tarif_treaty_basis_points": "",
            "document_type": "Bukti Pembayaran",
            "document_number": "INV-001",
            "document_date": "2026-01-15",
            "document_nitku": "",
        }
        for field, value in samples.items():
            ET.SubElement(row, field).text = value
        payload = ET.tostring(example, encoding="unicode", xml_declaration=True)
        return Response(
            content=payload,
            media_type="application/xml",
            headers={
                "Content-Disposition": f'attachment; filename="{prefix}_import_template.xml"'
            },
        )

    @router.get("/export-csv", dependencies=[_roles])
    def export_csv(
        current_user: CurrentUser,
        session: SessionDep,
        status_filter: Annotated[SlipStatus | None, Query(alias="status")] = None,
        tax_year: Annotated[int | None, Query(ge=2020, le=2100)] = None,
        tax_month: Annotated[int | None, Query(ge=1, le=12)] = None,
    ) -> Response:
        """Download CSV by Period — columns follow the e-Bupot list view."""
        slips = _export_slips(
            current_user,
            session,
            _period_conditions(status_filter, tax_year, tax_month),
        )

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(_CSV_COLUMNS)
        for slip in slips:
            writer.writerow(
                [
                    f"{slip.tax_month:02d}-{slip.tax_year}",
                    slip.withholding_number or "",
                    slip.status.value,
                    slip.electronic_signature_status,
                    slip.signer_name or "",
                    slip.calculation_method or "",
                    slip.document_nitku or "",
                    slip.tax_type or "",
                    slip.tax_object_code,
                    slip.recipient_identity_number,
                    slip.recipient_name,
                    slip.dpp,
                    slip.income_tax,
                    slip.tax_facility.value,
                ]
            )
        return Response(
            content=buffer.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{prefix}_export.csv"'},
        )

    @router.get("/export-xml", dependencies=[_roles])
    def export_xml(
        current_user: CurrentUser,
        session: SessionDep,
        status_filter: Annotated[SlipStatus | None, Query(alias="status")] = None,
        tax_year: Annotated[int | None, Query(ge=2020, le=2100)] = None,
        tax_month: Annotated[int | None, Query(ge=1, le=12)] = None,
    ) -> Response:
        """Monitoring → XML export of accessible slips."""
        slips = _export_slips(
            current_user,
            session,
            _period_conditions(status_filter, tax_year, tax_month),
        )

        root = ET.Element(f"{xml_tag}List")
        for slip in slips:
            row = ET.SubElement(root, xml_tag)
            data = slip_to_read(slip).model_dump()
            for field, value in data.items():
                child = ET.SubElement(row, field)
                if value is not None:
                    child.text = str(value.value if hasattr(value, "value") else value)
        payload = ET.tostring(root, encoding="unicode", xml_declaration=True)
        return Response(
            content=payload,
            media_type="application/xml",
            headers={"Content-Disposition": f'attachment; filename="{prefix}_export.xml"'},
        )

    @router.post("/import-xml", response_model=SlipImportResult, dependencies=[_write_roles])
    async def import_xml(
        file: UploadFile, current_user: CurrentUser, session: SessionDep
    ) -> SlipImportResult:
        """Impor data — accepts the XML format produced by GET /{type}/import-template."""
        raw = await file.read(MAX_IMPORT_BYTES + 1)
        if len(raw) > MAX_IMPORT_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File impor {label} terlalu besar",
            )
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Format XML tidak valid",
            ) from exc

        rows = root.findall(xml_tag)
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Tidak ada elemen <{xml_tag}> dalam file",
            )
        if len(rows) > MAX_IMPORT_ROWS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Maksimal {MAX_IMPORT_ROWS} baris {label} per impor",
            )

        results: list[SlipImportRowResult] = []
        for index, row in enumerate(rows, start=1):
            values: dict[str, str] = {}
            for child in row:
                tag = (child.tag or "").strip()
                if not tag or child.text is None or child.text.strip() == "":
                    continue
                key = tag
                if key not in _IMPORT_FIELDS:
                    alias = HEADER_ALIASES.get(tag.strip().lower().replace(" ", "_"))
                    if alias is None:
                        continue
                    key = alias
                if key not in values:
                    values[key] = child.text.strip()
            try:
                data = SlipCreate.model_validate(values)
                slip = _build_slip(data, current_user, session)
                session.add(slip)
                session.commit()
                session.refresh(slip)
                results.append(SlipImportRowResult(row=index, success=True, id=slip.id))
            except ValidationError as exc:
                session.rollback()
                first = exc.errors()[0]
                location = ".".join(str(part) for part in first["loc"])
                results.append(
                    SlipImportRowResult(
                        row=index, success=False, error=f"{location}: {first['msg']}"
                    )
                )
            except HTTPException as exc:
                session.rollback()
                results.append(SlipImportRowResult(row=index, success=False, error=str(exc.detail)))

        imported = sum(1 for item in results if item.success)
        return SlipImportResult(
            total_rows=len(results),
            imported=imported,
            failed=len(results) - imported,
            results=results,
        )

    @router.post(
        "",
        response_model=SlipRead,
        status_code=status.HTTP_201_CREATED,
        dependencies=[_write_roles],
    )
    def create_slip(data: SlipCreate, current_user: CurrentUser, session: SessionDep) -> SlipRead:
        slip = _build_slip(data, current_user, session)
        session.add(slip)
        session.flush()
        log_action(
            session,
            actor=current_user,
            action="document.create",
            entity=slip_type.value,
            entity_id=slip.id,
            tenant_id=slip.tenant_id,
            summary=f"Membuat {label} untuk {slip.recipient_name}",
        )
        session.commit()
        session.refresh(slip)
        return slip_to_read(slip)

    @router.post("/bulk-issue", response_model=SlipBulkIssueResult, dependencies=[_write_roles])
    def bulk_issue(
        data: SlipBulkIssue, current_user: CurrentUser, session: SessionDep
    ) -> SlipBulkIssueResult:
        """Bulk Process → Diterbitkan Masa Pajak: issue many drafts at once."""
        results: list[SlipImportRowResult] = []
        for slip_id in data.ids:
            slip = session.get(WithholdingSlip, slip_id)
            if (
                slip is None
                or slip.slip_type != slip_type
                or not can_read_slip(session, current_user, slip)
            ):
                results.append(
                    SlipImportRowResult(
                        row=slip_id, success=False, error=f"{label} tidak ditemukan"
                    )
                )
                continue
            if slip.status != SlipStatus.draft:
                results.append(
                    SlipImportRowResult(row=slip_id, success=False, error=f"{label} bukan draft")
                )
                continue
            issue_slip(
                slip,
                label,
                signer_id=current_user.id,
                signer_name=current_user.full_name,
            )
            session.add(slip)
            session.commit()
            session.refresh(slip)
            results.append(SlipImportRowResult(row=slip_id, success=True, id=slip.id))

        issued = sum(1 for item in results if item.success)
        return SlipBulkIssueResult(issued=issued, failed=len(results) - issued, results=results)

    @router.get("/{slip_id}", response_model=SlipRead, dependencies=[_roles])
    def get_slip(slip_id: int, current_user: CurrentUser, session: SessionDep) -> SlipRead:
        return slip_to_read(_get_accessible_slip(slip_id, current_user, session))

    @router.patch("/{slip_id}", response_model=SlipRead, dependencies=[_write_roles])
    def update_slip(
        slip_id: int, data: SlipUpdate, current_user: CurrentUser, session: SessionDep
    ) -> SlipRead:
        slip = _get_accessible_slip(slip_id, current_user, session)
        if slip.status != SlipStatus.draft:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"{label} yang sudah diterbitkan atau tidak valid tidak dapat diubah",
            )

        payload = data.model_dump(exclude_unset=True)
        class_id = payload.get("class_id", slip.class_id)
        siswa_id = payload.get("siswa_id", slip.siswa_id)
        tenant_id, resolved_class_id, resolved_siswa_id = resolve_document_scope(
            session=session,
            current_user=current_user,
            class_id=class_id,
            siswa_id=siswa_id,
            label=label,
        )
        slip.tenant_id = tenant_id
        slip.class_id = resolved_class_id
        slip.siswa_id = resolved_siswa_id

        if "rate_percent" in payload:
            slip.rate_basis_points = percent_to_basis_points(payload.pop("rate_percent"))
        if "dpp_percent" in payload:
            slip.dpp_rate_basis_points = percent_to_basis_points(payload.pop("dpp_percent"))
        for field, value in payload.items():
            if field in {"class_id", "siswa_id"}:
                continue
            setattr(slip, field, value)
        _validate_learning_fields(
            slip_type=slip_type,
            recipient_identity_number=slip.recipient_identity_number,
            withholder_npwp=slip.withholder_npwp,
            withholder_nitku=slip.withholder_nitku,
            recipient_nitku=slip.recipient_nitku,
            document_nitku=slip.document_nitku,
            tax_object_code=slip.tax_object_code,
            rate_percent=basis_points_to_percent(slip.rate_basis_points),
            kap_kjs=slip.kap_kjs,
            tax_facility=slip.tax_facility,
            document_number=slip.document_number,
            negara_treaty=slip.negara_treaty,
            pasal_treaty=slip.pasal_treaty,
            nomor_skd=slip.nomor_skd,
            tarif_treaty_basis_points=slip.tarif_treaty_basis_points,
            ter_category=slip.ter_category,
            tax_month=slip.tax_month,
        )
        slip.dpp = calculate_dpp(slip.gross_income, slip.dpp_rate_basis_points)
        income_tax, calculation_method = _resolve_income_tax(
            session,
            slip_type=slip_type,
            tax_nature=slip.tax_nature,
            tax_facility=slip.tax_facility,
            gross_income=slip.gross_income,
            dpp=slip.dpp,
            rate_basis_points=slip.rate_basis_points,
            ptkp_status=slip.ptkp_status,
            tax_year=slip.tax_year,
            negara_treaty=slip.negara_treaty,
            tarif_treaty_basis_points=slip.tarif_treaty_basis_points,
            recipient_identity_number=slip.recipient_identity_number,
            ter_category=slip.ter_category,
            tax_month=slip.tax_month,
        )
        slip.income_tax = income_tax
        slip.calculation_method = calculation_method
        session.add(slip)
        session.commit()
        session.refresh(slip)
        return slip_to_read(slip)

    @router.delete(
        "/{slip_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[_write_roles]
    )
    def delete_slip(slip_id: int, current_user: CurrentUser, session: SessionDep) -> None:
        """Hapus — only drafts (Belum Terbit) can be deleted."""
        slip = _get_accessible_slip(slip_id, current_user, session)
        if slip.status != SlipStatus.draft:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Hanya {label} draft yang dapat dihapus",
            )
        log_action(
            session,
            actor=current_user,
            action="document.delete",
            entity=slip_type.value,
            entity_id=slip.id,
            tenant_id=slip.tenant_id,
            summary=f"Menghapus {label} draft atas nama {slip.recipient_name}",
        )
        session.delete(slip)
        session.commit()

    @router.get("/{slip_id}/pdf", dependencies=[_roles])
    def download_slip_pdf(
        slip_id: int, current_user: CurrentUser, session: SessionDep
    ) -> Response:
        """Unduh BPE simulasi (PDF + QR internal) untuk dokumen terbit."""
        from app.services.slip_pdf import render_slip_pdf

        slip = _get_accessible_slip(slip_id, current_user, session)
        if slip.status != SlipStatus.issued:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"BPE simulasi hanya untuk {label} yang telah terbit",
            )
        pdf_bytes = render_slip_pdf(slip, label)
        filename = slip.withholding_number or f"{label}-{slip.id}"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="BPE-SIM-{filename}.pdf"'},
        )

    @router.post("/{slip_id}/issue", response_model=SlipRead, dependencies=[_write_roles])
    def issue(
        slip_id: int,
        current_user: CurrentUser,
        session: SessionDep,
        data: SlipIssue | None = None,
    ) -> SlipRead:
        slip = _get_accessible_slip(slip_id, current_user, session)
        if slip.status != SlipStatus.draft:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=f"{label} bukan draft"
            )

        signer_name = (
            (data.signer_name if data and data.signer_name else None) or current_user.full_name
        )
        if data and data.kode_otorisasi is not None and str(data.kode_otorisasi).strip() != "":
            if len(str(data.kode_otorisasi).strip()) < 6:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Kode Otorisasi simulasi minimal 6 karakter",
                )
        issue_slip(slip, label, signer_id=current_user.id, signer_name=signer_name)
        session.add(slip)
        log_action(
            session,
            actor=current_user,
            action="document.issue",
            entity=slip_type.value,
            entity_id=slip.id,
            tenant_id=slip.tenant_id,
            summary=(
                f"Menerbitkan {label} {slip.withholding_number} "
                f"atas nama {slip.recipient_name}"
            ),
        )
        session.commit()
        session.refresh(slip)
        return slip_to_read(slip)

    @router.post("/{slip_id}/cancel", response_model=SlipRead, dependencies=[_write_roles])
    def cancel(
        slip_id: int, data: SlipCancel, current_user: CurrentUser, session: SessionDep
    ) -> SlipRead:
        """Batal — an issued slip moves to Tidak Valid."""
        slip = _get_accessible_slip(slip_id, current_user, session)
        if slip.status != SlipStatus.issued:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Hanya {label} terbit yang dapat dibatalkan",
            )
        slip.status = SlipStatus.invalid
        slip.invalid_reason = data.reason or "Dibatalkan oleh pemotong"
        session.add(slip)
        log_action(
            session,
            actor=current_user,
            action="document.cancel",
            entity=slip_type.value,
            entity_id=slip.id,
            tenant_id=slip.tenant_id,
            summary=f"Membatalkan {label} {slip.withholding_number}",
        )
        session.commit()
        session.refresh(slip)
        return slip_to_read(slip)

    @router.post("/{slip_id}/invalidate", response_model=SlipRead, dependencies=[_review_roles])
    def invalidate(
        slip_id: int, data: SlipInvalidate, current_user: CurrentUser, session: SessionDep
    ) -> SlipRead:
        slip = _get_accessible_slip(slip_id, current_user, session)
        slip.status = SlipStatus.invalid
        slip.invalid_reason = data.invalid_reason
        session.add(slip)
        log_action(
            session,
            actor=current_user,
            action="document.invalidate",
            entity=slip_type.value,
            entity_id=slip.id,
            tenant_id=slip.tenant_id,
            summary=(
                f"Menandai tidak valid {label} {slip.withholding_number}: "
                f"{data.invalid_reason}"
            ),
        )
        session.commit()
        session.refresh(slip)
        return slip_to_read(slip)

    @router.patch("/{slip_id}/spt-flag", response_model=SlipRead, dependencies=[_review_roles])
    def update_spt_flag(
        slip_id: int, data: SlipSptFlagUpdate, current_user: CurrentUser, session: SessionDep
    ) -> SlipRead:
        """Set the issued-only SPT/objection lifecycle flag shown in Telah Terbit."""
        slip = _get_accessible_slip(slip_id, current_user, session)
        if slip.status != SlipStatus.issued:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Flag SPT hanya untuk {label} yang telah terbit",
            )
        slip.spt_flag = data.spt_flag
        session.add(slip)
        session.commit()
        session.refresh(slip)
        return slip_to_read(slip)

    @router.patch("/{slip_id}/review", response_model=SlipRead, dependencies=[_review_roles])
    def review(
        slip_id: int, data: SlipReview, current_user: CurrentUser, session: SessionDep
    ) -> SlipRead:
        slip = _get_accessible_slip(slip_id, current_user, session)
        payload = data.model_dump(exclude_unset=True)
        for field, value in payload.items():
            setattr(slip, field, value)
        session.add(slip)
        log_action(
            session,
            actor=current_user,
            action="document.review",
            entity=slip_type.value,
            entity_id=slip.id,
            tenant_id=slip.tenant_id,
            summary=f"Menilai {label} {slip.withholding_number} (skor {slip.score})",
        )
        session.commit()
        session.refresh(slip)
        return slip_to_read(slip)

    return router


bp21_router = make_slip_router(SlipType.bp21, "BP21")
bp23_router = make_slip_router(SlipType.bp23, "BP23")
bp26_router = make_slip_router(SlipType.bp26, "BP26")
bppu_router = make_slip_router(SlipType.bppu, "BPPU")
bpnr_router = make_slip_router(SlipType.bpnr, "BPNR")
sp_router = make_slip_router(SlipType.sp, "SP")
cy_router = make_slip_router(SlipType.cy, "CY")
