"""Simulated e-Bupot BP26 routes (PPh 26 — foreign tax subjects)."""

from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status
from pydantic import ValidationError
from sqlmodel import func, select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.models.bp26 import Bp26WithholdingSlip
from app.models.enums import Bp26SptFlag, Bp26Status, Bp26TaxFacility, Role
from app.routers._slip_common import (
    apply_access_filters,
    calculate_dpp,
    calculate_income_tax,
    can_read_slip,
    get_accessible_slip,
    issue_slip,
    percent_to_basis_points,
    resolve_slip_scope,
    slip_to_read,
)
from app.schemas.bp26 import (
    Bp26BulkIssue,
    Bp26BulkIssueResult,
    Bp26Cancel,
    Bp26Create,
    Bp26ImportResult,
    Bp26ImportRowResult,
    Bp26Invalidate,
    Bp26ListResponse,
    Bp26Read,
    Bp26Review,
    Bp26SptFlagUpdate,
    Bp26Summary,
    Bp26Update,
)

router = APIRouter(prefix="/bp26", tags=["bp26"])

_bp26_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru, Role.siswa))
_bp26_review_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru))

LABEL = "BP26"

_IMPORT_FIELDS = (
    "class_id",
    "tax_month",
    "tax_year",
    "recipient_identity_number",
    "recipient_name",
    "tax_type",
    "tax_object_code",
    "tax_object_name",
    "tax_nature",
    "tax_facility",
    "gross_income",
    "dpp_percent",
    "rate_percent",
    "kap_kjs",
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

MAX_IMPORT_BYTES = 1_000_000
MAX_IMPORT_ROWS = 500
MAX_EXPORT_ROWS = 5_000


def _to_read(slip: Bp26WithholdingSlip) -> Bp26Read:
    return slip_to_read(Bp26Read, slip)


def _get_accessible_slip(
    slip_id: int, current_user: CurrentUser, session: SessionDep
) -> Bp26WithholdingSlip:
    return get_accessible_slip(Bp26WithholdingSlip, slip_id, current_user, session, LABEL)


def _apply_access_filters(query, current_user):
    return apply_access_filters(query, current_user, Bp26WithholdingSlip)


def _period_conditions(
    status_filter: Bp26Status | None, tax_year: int | None, tax_month: int | None
) -> list:
    conditions = []
    if status_filter is not None:
        conditions.append(Bp26WithholdingSlip.status == status_filter)
    if tax_year is not None:
        conditions.append(Bp26WithholdingSlip.tax_year == tax_year)
    if tax_month is not None:
        conditions.append(Bp26WithholdingSlip.tax_month == tax_month)
    return conditions


def _build_slip(
    data: Bp26Create, current_user: CurrentUser, session: SessionDep
) -> Bp26WithholdingSlip:
    """Validate scope and construct an unsaved draft slip from a create payload."""
    tenant_id, class_id, siswa_id = resolve_slip_scope(
        session=session,
        current_user=current_user,
        class_id=data.class_id,
        siswa_id=data.siswa_id,
        label=LABEL,
    )
    rate_basis_points = percent_to_basis_points(data.rate_percent)
    dpp_rate_basis_points = percent_to_basis_points(data.dpp_percent)
    dpp = calculate_dpp(data.gross_income, dpp_rate_basis_points)
    return Bp26WithholdingSlip(
        tenant_id=tenant_id,
        class_id=class_id,
        siswa_id=siswa_id,
        created_by_id=current_user.id,
        tax_month=data.tax_month,
        tax_year=data.tax_year,
        recipient_identity_number=data.recipient_identity_number,
        recipient_name=data.recipient_name,
        tax_type=data.tax_type,
        tax_object_code=data.tax_object_code,
        tax_object_name=data.tax_object_name,
        tax_nature=data.tax_nature,
        tax_facility=data.tax_facility,
        gross_income=data.gross_income,
        dpp=dpp,
        dpp_rate_basis_points=dpp_rate_basis_points,
        rate_basis_points=rate_basis_points,
        income_tax=calculate_income_tax(dpp, rate_basis_points, data.tax_facility),
        kap_kjs=data.kap_kjs,
        document_type=data.document_type,
        document_number=data.document_number,
        document_date=data.document_date,
        document_nitku=data.document_nitku,
    )


@router.get("", response_model=Bp26ListResponse, dependencies=[_bp26_roles])
def list_bp26(
    current_user: CurrentUser,
    session: SessionDep,
    status_filter: Annotated[Bp26Status | None, Query(alias="status")] = None,
    spt_flag: Bp26SptFlag | None = None,
    tenant_id: int | None = None,
    class_id: int | None = None,
    siswa_id: int | None = None,
    tax_year: Annotated[int | None, Query(ge=2020, le=2100)] = None,
    tax_month: Annotated[int | None, Query(ge=1, le=12)] = None,
    tax_facility: Bp26TaxFacility | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Bp26ListResponse:
    conditions = _period_conditions(status_filter, tax_year, tax_month)
    if spt_flag is not None:
        conditions.append(Bp26WithholdingSlip.spt_flag == spt_flag)
    if current_user.role == Role.superadmin and tenant_id is not None:
        conditions.append(Bp26WithholdingSlip.tenant_id == tenant_id)
    if class_id is not None:
        conditions.append(Bp26WithholdingSlip.class_id == class_id)
    if siswa_id is not None and current_user.role != Role.siswa:
        conditions.append(Bp26WithholdingSlip.siswa_id == siswa_id)
    if tax_facility is not None:
        conditions.append(Bp26WithholdingSlip.tax_facility == tax_facility)

    base_count = _apply_access_filters(
        select(func.count()).select_from(Bp26WithholdingSlip).where(*conditions),
        current_user,
    )
    base_items = _apply_access_filters(
        select(Bp26WithholdingSlip)
        .where(*conditions)
        .order_by(Bp26WithholdingSlip.id.desc())
        .offset((page - 1) * size)
        .limit(size),
        current_user,
    )
    total = session.exec(base_count).one()
    items = session.exec(base_items).all()
    return Bp26ListResponse(
        items=[_to_read(item) for item in items],
        total=total,
        page=page,
        size=size,
    )


@router.get("/summary", response_model=Bp26Summary, dependencies=[_bp26_roles])
def bp26_summary(current_user: CurrentUser, session: SessionDep) -> Bp26Summary:
    query = _apply_access_filters(
        select(Bp26WithholdingSlip.status, func.count())
        .select_from(Bp26WithholdingSlip)
        .group_by(Bp26WithholdingSlip.status),
        current_user,
    )
    counts = dict(session.exec(query).all())
    return Bp26Summary(
        draft=counts.get(Bp26Status.draft, 0),
        issued=counts.get(Bp26Status.issued, 0),
        invalid=counts.get(Bp26Status.invalid, 0),
        total=sum(counts.values()),
    )


def _export_slips(current_user: CurrentUser, session: SessionDep, conditions: list):
    total = session.exec(
        _apply_access_filters(
            select(func.count()).select_from(Bp26WithholdingSlip).where(*conditions),
            current_user,
        )
    ).one()
    if total > MAX_EXPORT_ROWS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Terlalu banyak data untuk ekspor. Persempit filter masa pajak atau status.",
        )
    query = _apply_access_filters(
        select(Bp26WithholdingSlip)
        .where(*conditions)
        .order_by(Bp26WithholdingSlip.id)
        .limit(MAX_EXPORT_ROWS),
        current_user,
    )
    return session.exec(query).all()


@router.get("/import-template", dependencies=[_bp26_roles])
def bp26_import_template() -> Response:
    """Download the XML import format expected by POST /bp26/import-xml."""
    example = ET.Element("Bp26List")
    row = ET.SubElement(example, "Bp26")
    samples = {
        "class_id": "",
        "tax_month": "1",
        "tax_year": "2026",
        "recipient_identity_number": "1234567890123456",
        "recipient_name": "JOHN DOE",
        "tax_type": "PPh 26",
        "tax_object_code": "27-100-01",
        "tax_object_name": "Dividen yang diterima WPLN",
        "tax_nature": "final",
        "tax_facility": "none",
        "gross_income": "10000000",
        "dpp_percent": "100",
        "rate_percent": "20",
        "kap_kjs": "411127-100",
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
        headers={"Content-Disposition": 'attachment; filename="bp26_import_template.xml"'},
    )


@router.get("/export-csv", dependencies=[_bp26_roles])
def bp26_export_csv(
    current_user: CurrentUser,
    session: SessionDep,
    status_filter: Annotated[Bp26Status | None, Query(alias="status")] = None,
    tax_year: Annotated[int | None, Query(ge=2020, le=2100)] = None,
    tax_month: Annotated[int | None, Query(ge=1, le=12)] = None,
) -> Response:
    """Download CSV by Period — columns follow the BP26 list view."""
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
                slip.tax_type,
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
        headers={"Content-Disposition": 'attachment; filename="bp26_export.csv"'},
    )


@router.get("/export-xml", dependencies=[_bp26_roles])
def bp26_export_xml(
    current_user: CurrentUser,
    session: SessionDep,
    status_filter: Annotated[Bp26Status | None, Query(alias="status")] = None,
    tax_year: Annotated[int | None, Query(ge=2020, le=2100)] = None,
    tax_month: Annotated[int | None, Query(ge=1, le=12)] = None,
) -> Response:
    """Monitoring → XML export of accessible BP26 slips."""
    slips = _export_slips(
        current_user,
        session,
        _period_conditions(status_filter, tax_year, tax_month),
    )

    root = ET.Element("Bp26List")
    for slip in slips:
        row = ET.SubElement(root, "Bp26")
        data = _to_read(slip).model_dump()
        for field, value in data.items():
            child = ET.SubElement(row, field)
            if value is not None:
                child.text = str(value.value if hasattr(value, "value") else value)
    payload = ET.tostring(root, encoding="unicode", xml_declaration=True)
    return Response(
        content=payload,
        media_type="application/xml",
        headers={"Content-Disposition": 'attachment; filename="bp26_export.xml"'},
    )


@router.post(
    "/import-xml",
    response_model=Bp26ImportResult,
    dependencies=[_bp26_roles],
)
async def import_bp26_xml(
    file: UploadFile, current_user: CurrentUser, session: SessionDep
) -> Bp26ImportResult:
    """Impor data — accepts the XML format produced by GET /bp26/import-template."""
    raw = await file.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File impor BP26 terlalu besar",
        )
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Format XML tidak valid",
        ) from exc

    rows = root.findall("Bp26")
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Tidak ada elemen <Bp26> dalam file",
        )
    if len(rows) > MAX_IMPORT_ROWS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Maksimal {MAX_IMPORT_ROWS} baris BP26 per impor",
        )

    results: list[Bp26ImportRowResult] = []
    for index, row in enumerate(rows, start=1):
        values: dict[str, str] = {}
        for field in _IMPORT_FIELDS:
            node = row.find(field)
            if node is not None and node.text is not None and node.text.strip() != "":
                values[field] = node.text.strip()
        try:
            data = Bp26Create.model_validate(values)
            slip = _build_slip(data, current_user, session)
            session.add(slip)
            session.commit()
            session.refresh(slip)
            results.append(Bp26ImportRowResult(row=index, success=True, id=slip.id))
        except ValidationError as exc:
            session.rollback()
            first = exc.errors()[0]
            location = ".".join(str(part) for part in first["loc"])
            results.append(
                Bp26ImportRowResult(row=index, success=False, error=f"{location}: {first['msg']}")
            )
        except HTTPException as exc:
            session.rollback()
            results.append(Bp26ImportRowResult(row=index, success=False, error=str(exc.detail)))

    imported = sum(1 for item in results if item.success)
    return Bp26ImportResult(
        total_rows=len(results),
        imported=imported,
        failed=len(results) - imported,
        results=results,
    )


@router.post(
    "",
    response_model=Bp26Read,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_bp26_roles],
)
def create_bp26(data: Bp26Create, current_user: CurrentUser, session: SessionDep) -> Bp26Read:
    slip = _build_slip(data, current_user, session)
    session.add(slip)
    session.commit()
    session.refresh(slip)
    return _to_read(slip)


@router.post("/bulk-issue", response_model=Bp26BulkIssueResult, dependencies=[_bp26_roles])
def bulk_issue_bp26(
    data: Bp26BulkIssue, current_user: CurrentUser, session: SessionDep
) -> Bp26BulkIssueResult:
    """Bulk Process → Diterbitkan Masa Pajak: issue many drafts at once."""
    results: list[Bp26ImportRowResult] = []
    for slip_id in data.ids:
        slip = session.get(Bp26WithholdingSlip, slip_id)
        if slip is None or not can_read_slip(session, current_user, slip):
            results.append(
                Bp26ImportRowResult(row=slip_id, success=False, error="BP26 tidak ditemukan")
            )
            continue
        if slip.status != Bp26Status.draft:
            results.append(
                Bp26ImportRowResult(row=slip_id, success=False, error="BP26 bukan draft")
            )
            continue
        issue_slip(slip, LABEL)
        session.add(slip)
        session.commit()
        session.refresh(slip)
        results.append(Bp26ImportRowResult(row=slip_id, success=True, id=slip.id))

    issued = sum(1 for item in results if item.success)
    return Bp26BulkIssueResult(issued=issued, failed=len(results) - issued, results=results)


@router.get("/{slip_id}", response_model=Bp26Read, dependencies=[_bp26_roles])
def get_bp26(slip_id: int, current_user: CurrentUser, session: SessionDep) -> Bp26Read:
    return _to_read(_get_accessible_slip(slip_id, current_user, session))


@router.patch("/{slip_id}", response_model=Bp26Read, dependencies=[_bp26_roles])
def update_bp26(
    slip_id: int, data: Bp26Update, current_user: CurrentUser, session: SessionDep
) -> Bp26Read:
    slip = _get_accessible_slip(slip_id, current_user, session)
    if slip.status != Bp26Status.draft:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="BP26 yang sudah diterbitkan atau tidak valid tidak dapat diubah",
        )

    payload = data.model_dump(exclude_unset=True)
    class_id = payload.get("class_id", slip.class_id)
    siswa_id = payload.get("siswa_id", slip.siswa_id)
    tenant_id, resolved_class_id, resolved_siswa_id = resolve_slip_scope(
        session=session,
        current_user=current_user,
        class_id=class_id,
        siswa_id=siswa_id,
        label=LABEL,
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
    slip.income_tax = calculate_income_tax(slip.dpp, slip.rate_basis_points, slip.tax_facility)
    session.add(slip)
    session.commit()
    session.refresh(slip)
    return _to_read(slip)


@router.delete("/{slip_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[_bp26_roles])
def delete_bp26(slip_id: int, current_user: CurrentUser, session: SessionDep) -> None:
    """Hapus — only drafts (Belum Terbit) can be deleted."""
    slip = _get_accessible_slip(slip_id, current_user, session)
    if slip.status != Bp26Status.draft:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Hanya BP26 draft yang dapat dihapus",
        )
    session.delete(slip)
    session.commit()


@router.post("/{slip_id}/issue", response_model=Bp26Read, dependencies=[_bp26_roles])
def issue_bp26(slip_id: int, current_user: CurrentUser, session: SessionDep) -> Bp26Read:
    slip = _get_accessible_slip(slip_id, current_user, session)
    if slip.status != Bp26Status.draft:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="BP26 bukan draft")

    issue_slip(slip, LABEL)
    session.add(slip)
    session.commit()
    session.refresh(slip)
    return _to_read(slip)


@router.post("/{slip_id}/cancel", response_model=Bp26Read, dependencies=[_bp26_roles])
def cancel_bp26(
    slip_id: int, data: Bp26Cancel, current_user: CurrentUser, session: SessionDep
) -> Bp26Read:
    """Batal — an issued slip moves to Tidak Valid."""
    slip = _get_accessible_slip(slip_id, current_user, session)
    if slip.status != Bp26Status.issued:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Hanya BP26 terbit yang dapat dibatalkan",
        )
    slip.status = Bp26Status.invalid
    slip.invalid_reason = data.reason or "Dibatalkan oleh pemotong"
    session.add(slip)
    session.commit()
    session.refresh(slip)
    return _to_read(slip)


@router.post(
    "/{slip_id}/invalidate",
    response_model=Bp26Read,
    dependencies=[_bp26_review_roles],
)
def invalidate_bp26(
    slip_id: int, data: Bp26Invalidate, current_user: CurrentUser, session: SessionDep
) -> Bp26Read:
    slip = _get_accessible_slip(slip_id, current_user, session)
    slip.status = Bp26Status.invalid
    slip.invalid_reason = data.invalid_reason
    session.add(slip)
    session.commit()
    session.refresh(slip)
    return _to_read(slip)


@router.patch("/{slip_id}/spt-flag", response_model=Bp26Read, dependencies=[_bp26_review_roles])
def update_bp26_spt_flag(
    slip_id: int, data: Bp26SptFlagUpdate, current_user: CurrentUser, session: SessionDep
) -> Bp26Read:
    """Set the issued-only SPT/objection lifecycle flag shown in Telah Terbit."""
    slip = _get_accessible_slip(slip_id, current_user, session)
    if slip.status != Bp26Status.issued:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Flag SPT hanya untuk BP26 yang telah terbit",
        )
    slip.spt_flag = data.spt_flag
    session.add(slip)
    session.commit()
    session.refresh(slip)
    return _to_read(slip)


@router.patch("/{slip_id}/review", response_model=Bp26Read, dependencies=[_bp26_review_roles])
def review_bp26(
    slip_id: int, data: Bp26Review, current_user: CurrentUser, session: SessionDep
) -> Bp26Read:
    slip = _get_accessible_slip(slip_id, current_user, session)
    payload = data.model_dump(exclude_unset=True)
    for field, value in payload.items():
        setattr(slip, field, value)
    session.add(slip)
    session.commit()
    session.refresh(slip)
    return _to_read(slip)
