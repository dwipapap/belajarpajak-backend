"""Simulated e-Bupot withholding slip routes — one factory for all slip types.

BP21 and BP26 are the same form with different data values, so both mount the
identical endpoint set; ``slip_type`` scopes every query. Adding a new bupot
type is one enum value + one ``make_slip_router`` call.
"""

from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status
from pydantic import ValidationError
from sqlmodel import Session, func, select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.models.enrollment import Enrollment
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
    SlipListResponse,
    SlipRead,
    SlipReview,
    SlipSptFlagUpdate,
    SlipSummary,
    SlipUpdate,
)

MAX_IMPORT_BYTES = 1_000_000
MAX_IMPORT_ROWS = 500
MAX_EXPORT_ROWS = 5_000

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


def percent_to_basis_points(percent: float) -> int:
    rate = Decimal(str(percent))
    return int((rate * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def basis_points_to_percent(basis_points: int) -> float:
    return float((Decimal(basis_points) / Decimal("100")).quantize(Decimal("0.01")))


def calculate_dpp(gross_income: int, dpp_rate_basis_points: int) -> int:
    value = Decimal(gross_income) * Decimal(dpp_rate_basis_points) / Decimal("10000")
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def calculate_income_tax(dpp: int, rate_basis_points: int, facility: SlipTaxFacility) -> int:
    if facility in (SlipTaxFacility.skb, SlipTaxFacility.rate_0):
        return 0
    tax = Decimal(dpp) * Decimal(rate_basis_points) / Decimal("10000")
    return int(tax.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _tax_by_basis_points(amount: int, rate_basis_points: int) -> int:
    tax = Decimal(amount) * Decimal(rate_basis_points) / Decimal("10000")
    return int(tax.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


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
            TierPtkp.is_active == True,
        )
    ).one_or_none()
    if ptkp is None:
        return None

    brackets = session.exec(
        select(TarifProgresifPasal17)
        .where(
            TarifProgresifPasal17.tahun_pajak == tax_year,
            TarifProgresifPasal17.is_active == True,
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
) -> int:
    if tax_facility in (SlipTaxFacility.skb, SlipTaxFacility.rate_0):
        return 0

    if slip_type == SlipType.bp21 and tax_nature == SlipTaxNature.non_final:
        progressive_tax = _calculate_bp21_progressive_tax(
            session,
            gross_income=gross_income,
            ptkp_status=ptkp_status,
            tax_year=tax_year,
        )
        if progressive_tax is not None:
            return progressive_tax

    if (
        slip_type == SlipType.bp26
        and tax_nature == SlipTaxNature.non_final
        and negara_treaty
        and tarif_treaty_basis_points is not None
    ):
        return _tax_by_basis_points(dpp, tarif_treaty_basis_points)

    return calculate_income_tax(dpp, rate_basis_points, tax_facility)


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


def issue_slip(slip: WithholdingSlip, label: str) -> None:
    slip.status = SlipStatus.issued
    slip.issued_at = datetime.now(UTC)
    slip.electronic_signature_status = "signed"
    slip.withholding_number = f"{label}-{slip.tax_year}{slip.tax_month:02d}-{slip.id:06d}"


def is_enrolled(session: Session, *, class_id: int, siswa_id: int) -> bool:
    return (
        session.exec(
            select(Enrollment).where(
                Enrollment.class_id == class_id,
                Enrollment.siswa_id == siswa_id,
            )
        ).first()
        is not None
    )


def resolve_slip_scope(
    *,
    session: Session,
    current_user: User,
    class_id: int | None,
    siswa_id: int | None,
    label: str,
) -> tuple[int, int | None, int]:
    """Resolve and validate tenant/class/student scope for slip writes."""
    if current_user.role == Role.siswa:
        siswa_id = current_user.id

    if siswa_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"siswa_id wajib untuk membuat {label} selain dari akun siswa",
        )

    siswa = session.get(User, siswa_id)
    if siswa is None or siswa.role != Role.siswa:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="siswa_id harus merujuk ke akun siswa",
        )

    if (
        current_user.role not in (Role.superadmin, Role.siswa)
        and siswa.tenant_id != current_user.tenant_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses lintas tenant ditolak",
        )

    tenant_id = siswa.tenant_id
    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Siswa wajib berada dalam tenant",
        )

    if class_id is not None:
        school_class = session.get(SchoolClass, class_id)
        if school_class is None or school_class.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Kelas tidak ditemukan",
            )
        if current_user.role == Role.guru and school_class.guru_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bukan kelas Anda")
        if current_user.role == Role.siswa and not is_enrolled(
            session, class_id=class_id, siswa_id=current_user.id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Anda tidak terdaftar di kelas ini",
            )
        if not is_enrolled(session, class_id=class_id, siswa_id=siswa_id):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Siswa belum terdaftar pada kelas ini",
            )
    elif current_user.role == Role.guru:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Guru wajib memilih kelas untuk {label}",
        )

    return tenant_id, class_id, siswa_id


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
    _review_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru))

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
        tenant_id, class_id, siswa_id = resolve_slip_scope(
            session=session,
            current_user=current_user,
            class_id=data.class_id,
            siswa_id=data.siswa_id,
            label=label,
        )
        rate_basis_points = percent_to_basis_points(data.rate_percent)
        dpp_rate_basis_points = percent_to_basis_points(data.dpp_percent)
        dpp = calculate_dpp(data.gross_income, dpp_rate_basis_points)
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
            income_tax=_resolve_income_tax(
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
            ),
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
        samples = {
            "class_id": "",
            "tax_month": "1",
            "tax_year": "2026",
            "recipient_identity_number": "1234567890123456",
            "recipient_name": "JOHN DOE",
            "tax_type": "PPh 26" if slip_type == SlipType.bp26 else "PPh 21",
            "tax_object_code": "27-100-01" if slip_type == SlipType.bp26 else "21-100-01",
            "tax_object_name": "Dividen yang diterima WPLN"
            if slip_type == SlipType.bp26
            else "Imbalan kepada bukan pegawai",
            "tax_nature": "final" if slip_type == SlipType.bp26 else "non_final",
            "tax_facility": "none",
            "gross_income": "10000000",
            "dpp_percent": "100",
            "rate_percent": "20" if slip_type == SlipType.bp26 else "5",
            "kap_kjs": "411127-100" if slip_type == SlipType.bp26 else "411121-100",
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

    @router.post("/import-xml", response_model=SlipImportResult, dependencies=[_roles])
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
            for field in _IMPORT_FIELDS:
                node = row.find(field)
                if node is not None and node.text is not None and node.text.strip() != "":
                    values[field] = node.text.strip()
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
        dependencies=[_roles],
    )
    def create_slip(data: SlipCreate, current_user: CurrentUser, session: SessionDep) -> SlipRead:
        slip = _build_slip(data, current_user, session)
        session.add(slip)
        session.commit()
        session.refresh(slip)
        return slip_to_read(slip)

    @router.post("/bulk-issue", response_model=SlipBulkIssueResult, dependencies=[_roles])
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
            issue_slip(slip, label)
            session.add(slip)
            session.commit()
            session.refresh(slip)
            results.append(SlipImportRowResult(row=slip_id, success=True, id=slip.id))

        issued = sum(1 for item in results if item.success)
        return SlipBulkIssueResult(issued=issued, failed=len(results) - issued, results=results)

    @router.get("/{slip_id}", response_model=SlipRead, dependencies=[_roles])
    def get_slip(slip_id: int, current_user: CurrentUser, session: SessionDep) -> SlipRead:
        return slip_to_read(_get_accessible_slip(slip_id, current_user, session))

    @router.patch("/{slip_id}", response_model=SlipRead, dependencies=[_roles])
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
        tenant_id, resolved_class_id, resolved_siswa_id = resolve_slip_scope(
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
        slip.dpp = calculate_dpp(slip.gross_income, slip.dpp_rate_basis_points)
        slip.income_tax = _resolve_income_tax(
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
        )
        session.add(slip)
        session.commit()
        session.refresh(slip)
        return slip_to_read(slip)

    @router.delete("/{slip_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[_roles])
    def delete_slip(slip_id: int, current_user: CurrentUser, session: SessionDep) -> None:
        """Hapus — only drafts (Belum Terbit) can be deleted."""
        slip = _get_accessible_slip(slip_id, current_user, session)
        if slip.status != SlipStatus.draft:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Hanya {label} draft yang dapat dihapus",
            )
        session.delete(slip)
        session.commit()

    @router.post("/{slip_id}/issue", response_model=SlipRead, dependencies=[_roles])
    def issue(slip_id: int, current_user: CurrentUser, session: SessionDep) -> SlipRead:
        slip = _get_accessible_slip(slip_id, current_user, session)
        if slip.status != SlipStatus.draft:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=f"{label} bukan draft"
            )

        issue_slip(slip, label)
        session.add(slip)
        session.commit()
        session.refresh(slip)
        return slip_to_read(slip)

    @router.post("/{slip_id}/cancel", response_model=SlipRead, dependencies=[_roles])
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
        session.commit()
        session.refresh(slip)
        return slip_to_read(slip)

    return router


bp21_router = make_slip_router(SlipType.bp21, "BP21")
bp26_router = make_slip_router(SlipType.bp26, "BP26")
