"""Simulated e-Faktur Pajak Keluaran routes.

Only one document type lives here, so there is no router factory — just one
``APIRouter``. What is different from e-Bupot is the shape: a faktur owns many
Detail Transaksi lines, and every stored amount is derived from them. Clients
send prices, quantities and rates; the server computes total_price, DPP, PPN and
PPnBM and recomputes the header totals on every mutation, so a student's faktur
can never carry arithmetic that disagrees with the rules being taught.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import text as sa_text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.core.money import apply_basis_points, basis_points_to_percent, percent_to_basis_points
from app.core.scope import resolve_document_scope
from app.models.enums import BuyerIdentityType, Role, TaxInvoiceKind, TaxInvoiceStatus
from app.models.school_class import SchoolClass
from app.models.tax_invoice import TaxInvoice, TaxInvoiceLine
from app.models.user import User
from app.schemas.tax_invoice import (
    DPP_OTHER_CODES,
    TaxInvoiceBulkIssue,
    TaxInvoiceBulkIssueResult,
    TaxInvoiceCancel,
    TaxInvoiceCreate,
    TaxInvoiceLineCreate,
    TaxInvoiceLineRead,
    TaxInvoiceLineUpdate,
    TaxInvoiceListResponse,
    TaxInvoiceRead,
    TaxInvoiceReview,
    TaxInvoiceSummary,
    TaxInvoiceUpdate,
)

LABEL = "Faktur Pajak Keluaran"
MAX_EXPORT_ROWS = 5_000

#: DPP Nilai Lain is 11/12 of the selling price (PMK 131/2024), so a 12% rate
#: over that base yields the same 11% effective PPN.
DPP_OTHER_NUMERATOR = Decimal("11")
DPP_OTHER_DENOMINATOR = Decimal("12")

_CSV_COLUMNS = (
    "Nomor Faktur",
    "Tanggal Faktur",
    "Masa Pajak",
    "Tahun",
    "Kode Transaksi",
    "Jenis Faktur",
    "Status",
    "NPWP Pembeli / Identitas lainnya",
    "Nama Pembeli",
    "Harga Jual/DPP (Rp)",
    "DPP Nilai Lain (Rp)",
    "PPN (Rp)",
    "PPnBM (Rp)",
)

router = APIRouter(prefix="/faktur-keluaran", tags=["faktur-keluaran"])

_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru, Role.siswa))
_review_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru))
# Membatalkan dokumen adalah tindakan penjual (tenant) — siswa dan guru hanya
# meninjau; hanya admin/superadmin tenant yang boleh membatalkan.
_cancel_roles = Depends(require_roles(Role.superadmin, Role.admin))


# --- tax engine ---------------------------------------------------------------


def quantity_to_milli(quantity: Decimal) -> int:
    value = quantity * Decimal("1000")
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def milli_to_quantity(quantity_milli: int) -> float:
    return float((Decimal(quantity_milli) / Decimal("1000")).quantize(Decimal("0.001")))


def dpp_nilai_lain(dpp: int) -> int:
    value = Decimal(dpp) * DPP_OTHER_NUMERATOR / DPP_OTHER_DENOMINATOR
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def calculate_line(line: TaxInvoiceLine) -> None:
    """Fill a line's derived amounts in place from its inputs."""
    total = Decimal(line.unit_price) * Decimal(line.quantity_milli) / Decimal("1000")
    line.total_price = int(total.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    line.dpp = max(line.total_price - line.discount, 0)

    if line.use_dpp_other:
        line.dpp_other = dpp_nilai_lain(line.dpp)
        line.ppn = apply_basis_points(line.dpp_other, line.ppn_rate_basis_points)
    else:
        line.dpp_other = 0
        line.ppn = apply_basis_points(line.dpp, line.ppn_rate_basis_points)

    line.ppnbm = apply_basis_points(line.dpp, line.ppnbm_rate_basis_points)


def recalculate_invoice(session: Session, invoice: TaxInvoice) -> None:
    """Recompute every line and roll the results up into the header totals."""
    lines = _invoice_lines(session, invoice.id)
    forced = invoice.transaction_code in DPP_OTHER_CODES
    for line in lines:
        # Kode transaksi 04/05 always use DPP Nilai Lain, whatever the form sent.
        if forced:
            line.use_dpp_other = True
        calculate_line(line)
        session.add(line)

    invoice.total_price = sum(line.total_price for line in lines)
    invoice.total_discount = sum(line.discount for line in lines)
    invoice.total_dpp = sum(line.dpp for line in lines)
    invoice.total_dpp_other = sum(line.dpp_other for line in lines)
    invoice.total_ppn = sum(line.ppn for line in lines)
    invoice.total_ppnbm = sum(line.ppnbm for line in lines)
    session.add(invoice)


def build_line(data: TaxInvoiceLineCreate, *, invoice_id: int, line_order: int) -> TaxInvoiceLine:
    return TaxInvoiceLine(
        invoice_id=invoice_id,
        line_order=line_order,
        line_type=data.line_type,
        item_code=data.item_code,
        item_name=data.item_name,
        unit=data.unit,
        unit_price=data.unit_price,
        quantity_milli=quantity_to_milli(data.quantity),
        discount=data.discount,
        use_dpp_other=data.use_dpp_other,
        ppn_rate_basis_points=percent_to_basis_points(data.ppn_rate_percent),
        ppnbm_rate_basis_points=percent_to_basis_points(data.ppnbm_rate_percent),
    )


def generate_invoice_number(session: Session, invoice: TaxInvoice) -> str:
    """16-digit Nomor Faktur: kode transaksi + status + 000 + YY + 8-digit serial.

    The serial runs per tenant per tax year — as in the real system, numbering
    belongs to the taxpayer, so two tenants both start at 1. A PostgreSQL
    transaction-level advisory lock keyed on ``(tenant_id, tax_year)`` serialises
    concurrent issues for the same taxpayer + period, so ``MAX(serial) + 1`` is
    computed while no peer can race. The lock auto-releases on commit/rollback.
    The (tenant_id, invoice_number) unique constraint stays as a final backstop;
    callers translate any IntegrityError into a 409.
    """
    status_digit = "1" if invoice.invoice_kind == TaxInvoiceKind.pengganti else "0"
    year_suffix = f"{invoice.tax_year % 100:02d}"
    prefix = f"{invoice.transaction_code}{status_digit}000{year_suffix}"

    session.execute(
        sa_text("SELECT pg_advisory_xact_lock(:key1, :key2)"),
        {"key1": invoice.tenant_id, "key2": 10_000_000 + invoice.tax_year},
    )

    used = session.exec(
        select(TaxInvoice.invoice_number).where(
            TaxInvoice.tenant_id == invoice.tenant_id,
            TaxInvoice.tax_year == invoice.tax_year,
            TaxInvoice.invoice_number.is_not(None),
        )
    ).all()
    serials = [int(number[-8:]) for number in used if number and number[-8:].isdigit()]
    return f"{prefix}{max(serials, default=0) + 1:08d}"


# --- access helpers -----------------------------------------------------------


def can_read_invoice(session: Session, current_user: User, invoice: TaxInvoice) -> bool:
    if current_user.role == Role.superadmin:
        return True
    if current_user.role == Role.admin:
        return invoice.tenant_id == current_user.tenant_id
    if current_user.role == Role.siswa:
        return invoice.siswa_id == current_user.id
    if current_user.role == Role.guru and invoice.class_id is not None:
        school_class = session.get(SchoolClass, invoice.class_id)
        return school_class is not None and school_class.guru_id == current_user.id
    return False


def apply_access_filters(query, current_user: User):
    if current_user.role == Role.superadmin:
        return query
    if current_user.role == Role.admin:
        return query.where(TaxInvoice.tenant_id == current_user.tenant_id)
    if current_user.role == Role.siswa:
        return query.where(TaxInvoice.siswa_id == current_user.id)

    taught_class_ids = select(SchoolClass.id).where(SchoolClass.guru_id == current_user.id)
    return query.where(TaxInvoice.class_id.in_(taught_class_ids))


def _invoice_lines(session: Session, invoice_id: int | None) -> list[TaxInvoiceLine]:
    if invoice_id is None:
        return []
    return list(
        session.exec(
            select(TaxInvoiceLine)
            .where(TaxInvoiceLine.invoice_id == invoice_id)
            .order_by(TaxInvoiceLine.line_order, TaxInvoiceLine.id)
        ).all()
    )


def line_to_read(line: TaxInvoiceLine) -> TaxInvoiceLineRead:
    return TaxInvoiceLineRead.model_validate(
        {
            **line.model_dump(),
            "quantity": milli_to_quantity(line.quantity_milli),
            "ppn_rate_percent": basis_points_to_percent(line.ppn_rate_basis_points),
            "ppnbm_rate_percent": basis_points_to_percent(line.ppnbm_rate_basis_points),
        }
    )


def invoice_to_read(session: Session, invoice: TaxInvoice) -> TaxInvoiceRead:
    return TaxInvoiceRead.model_validate(
        {
            **invoice.model_dump(),
            "lines": [line_to_read(line) for line in _invoice_lines(session, invoice.id)],
        }
    )


def _get_accessible_invoice(
    invoice_id: int, current_user: CurrentUser, session: SessionDep
) -> TaxInvoice:
    invoice = session.get(TaxInvoice, invoice_id)
    if invoice is None or not can_read_invoice(session, current_user, invoice):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"{LABEL} tidak ditemukan"
        )
    return invoice


def _require_draft(invoice: TaxInvoice, action: str) -> None:
    if invoice.status != TaxInvoiceStatus.draft:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Hanya {LABEL} draft yang dapat {action}",
        )


def _validate_tax_period(invoice_date: date, tax_month: int, tax_year: int) -> None:
    """Tanggal Faktur must fall inside the chosen Masa Pajak / Tahun Pajak."""
    if invoice_date.month != tax_month or invoice_date.year != tax_year:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Tanggal faktur harus sesuai dengan masa pajak yang dipilih",
        )


def _reject_pengganti(kind: TaxInvoiceKind) -> None:
    """Jenis faktur pengganti is not implemented yet — keep it disabled."""
    if kind == TaxInvoiceKind.pengganti:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Jenis faktur pengganti belum didukung",
        )


def _validate_identity(
    identity_type: BuyerIdentityType,
    identity_number: str | None,
    document_number: str | None,
) -> None:
    """Simulator identity rules for the pembeli.

    - NPWP: numeric (15–16 digits, separators allowed).
    - NIK: exactly 16 digits.
    - Paspor / Identitas Lain: both an identity number and a document number.
    These are the server-side authority; the frontend merely mirrors them for UX.
    """
    number = (identity_number or "").strip()
    doc = (document_number or "").strip()

    if identity_type == BuyerIdentityType.nik:
        if not number.isdigit() or len(number) != 16:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="NIK harus 16 digit angka",
            )
    elif identity_type == BuyerIdentityType.npwp:
        cleaned = number.replace(".", "").replace("-", "")
        if not cleaned or not cleaned.isdigit() or not (15 <= len(cleaned) <= 16):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Nomor NPWP tidak valid",
            )
    else:  # paspor / identitas_lain
        if not number:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Nomor identitas wajib diisi untuk paspor/identitas lain",
            )
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Nomor dokumen wajib diisi untuk paspor/identitas lain",
            )


def issue_invoice(session: Session, invoice: TaxInvoice) -> None:
    invoice.status = TaxInvoiceStatus.issued
    invoice.issued_at = datetime.now(UTC)
    invoice.electronic_signature_status = "signed"
    invoice.invoice_number = generate_invoice_number(session, invoice)


def _replace_lines(
    session: Session, invoice: TaxInvoice, lines: list[TaxInvoiceLineCreate]
) -> None:
    for existing in _invoice_lines(session, invoice.id):
        session.delete(existing)
    session.flush()
    for order, line_data in enumerate(lines):
        session.add(build_line(line_data, invoice_id=invoice.id, line_order=order))
    session.flush()


def _invoice_conditions(
    *,
    status: TaxInvoiceStatus | None = None,
    tenant_id: int | None = None,
    class_id: int | None = None,
    siswa_id: int | None = None,
    tax_year: int | None = None,
    tax_month: int | None = None,
    transaction_code: str | None = None,
    buyer_name: str | None = None,
    buyer_identity_number: str | None = None,
    current_user: User,
) -> list:
    """Shared filter builder for the list view and the CSV export.

    Keeping list and export on one builder means an export can never diverge
    from what the user sees, and tenant/class/student scoping stays server-side.
    """
    conditions = []
    if status is not None:
        conditions.append(TaxInvoice.status == status)
    if tax_year is not None:
        conditions.append(TaxInvoice.tax_year == tax_year)
    if tax_month is not None:
        conditions.append(TaxInvoice.tax_month == tax_month)
    if transaction_code is not None:
        conditions.append(TaxInvoice.transaction_code == transaction_code)
    if buyer_name:
        conditions.append(TaxInvoice.buyer_name.ilike(f"%{buyer_name}%"))
    if buyer_identity_number:
        conditions.append(TaxInvoice.buyer_identity_number.ilike(f"%{buyer_identity_number}%"))
    if current_user.role == Role.superadmin and tenant_id is not None:
        conditions.append(TaxInvoice.tenant_id == tenant_id)
    if class_id is not None:
        conditions.append(TaxInvoice.class_id == class_id)
    if siswa_id is not None and current_user.role != Role.siswa:
        conditions.append(TaxInvoice.siswa_id == siswa_id)
    return conditions


# --- endpoints ----------------------------------------------------------------


@router.get("", response_model=TaxInvoiceListResponse, dependencies=[_roles])
def list_invoices(
    current_user: CurrentUser,
    session: SessionDep,
    status_filter: Annotated[TaxInvoiceStatus | None, Query(alias="status")] = None,
    tenant_id: int | None = None,
    class_id: int | None = None,
    siswa_id: int | None = None,
    tax_year: Annotated[int | None, Query(ge=2020, le=2100)] = None,
    tax_month: Annotated[int | None, Query(ge=1, le=12)] = None,
    transaction_code: Annotated[str | None, Query(pattern=r"^(0[1-9]|10)$")] = None,
    buyer_name: str | None = None,
    buyer_identity_number: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TaxInvoiceListResponse:
    conditions = _invoice_conditions(
        status=status_filter,
        tenant_id=tenant_id,
        class_id=class_id,
        siswa_id=siswa_id,
        tax_year=tax_year,
        tax_month=tax_month,
        transaction_code=transaction_code,
        buyer_name=buyer_name,
        buyer_identity_number=buyer_identity_number,
        current_user=current_user,
    )

    total = session.exec(
        apply_access_filters(
            select(func.count()).select_from(TaxInvoice).where(*conditions), current_user
        )
    ).one()
    items = session.exec(
        apply_access_filters(
            select(TaxInvoice)
            .where(*conditions)
            .order_by(TaxInvoice.id.desc())
            .offset((page - 1) * size)
            .limit(size),
            current_user,
        )
    ).all()
    return TaxInvoiceListResponse(
        items=[invoice_to_read(session, item) for item in items],
        total=total,
        page=page,
        size=size,
    )


@router.get("/summary", response_model=TaxInvoiceSummary, dependencies=[_roles])
def invoice_summary(current_user: CurrentUser, session: SessionDep) -> TaxInvoiceSummary:
    query = apply_access_filters(
        select(TaxInvoice.status, func.count()).select_from(TaxInvoice).group_by(TaxInvoice.status),
        current_user,
    )
    counts = dict(session.exec(query).all())
    return TaxInvoiceSummary(
        draft=counts.get(TaxInvoiceStatus.draft, 0),
        issued=counts.get(TaxInvoiceStatus.issued, 0),
        invalid=counts.get(TaxInvoiceStatus.invalid, 0),
        total=sum(counts.values()),
    )


@router.get("/export-csv", dependencies=[_roles])
def export_csv(
    current_user: CurrentUser,
    session: SessionDep,
    status_filter: Annotated[TaxInvoiceStatus | None, Query(alias="status")] = None,
    tenant_id: int | None = None,
    class_id: int | None = None,
    siswa_id: int | None = None,
    tax_year: Annotated[int | None, Query(ge=2020, le=2100)] = None,
    tax_month: Annotated[int | None, Query(ge=1, le=12)] = None,
    transaction_code: Annotated[str | None, Query(pattern=r"^(0[1-9]|10)$")] = None,
    buyer_name: str | None = None,
    buyer_identity_number: str | None = None,
) -> Response:
    """Download CSV — same filters as the list view, driven by the same builder.

    Server-side tenant/class/student scoping stays intact, so an export can
    never leak data from outside the caller's scope.
    """
    conditions = _invoice_conditions(
        status=status_filter,
        tenant_id=tenant_id,
        class_id=class_id,
        siswa_id=siswa_id,
        tax_year=tax_year,
        tax_month=tax_month,
        transaction_code=transaction_code,
        buyer_name=buyer_name,
        buyer_identity_number=buyer_identity_number,
        current_user=current_user,
    )

    total = session.exec(
        apply_access_filters(
            select(func.count()).select_from(TaxInvoice).where(*conditions), current_user
        )
    ).one()
    if total > MAX_EXPORT_ROWS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Terlalu banyak data untuk ekspor. Persempit filter masa pajak atau status.",
        )

    invoices = session.exec(
        apply_access_filters(
            select(TaxInvoice).where(*conditions).order_by(TaxInvoice.id).limit(MAX_EXPORT_ROWS),
            current_user,
        )
    ).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_CSV_COLUMNS)
    for invoice in invoices:
        writer.writerow(
            [
                invoice.invoice_number or "",
                invoice.invoice_date.isoformat(),
                invoice.tax_month,
                invoice.tax_year,
                invoice.transaction_code,
                invoice.invoice_kind.value,
                invoice.status.value,
                invoice.buyer_identity_number,
                invoice.buyer_name,
                invoice.total_dpp,
                invoice.total_dpp_other,
                invoice.total_ppn,
                invoice.total_ppnbm,
            ]
        )
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="faktur_keluaran_export.csv"'},
    )


@router.post(
    "",
    response_model=TaxInvoiceRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_roles],
)
def create_invoice(
    data: TaxInvoiceCreate, current_user: CurrentUser, session: SessionDep
) -> TaxInvoiceRead:
    tenant_id, class_id, siswa_id = resolve_document_scope(
        session=session,
        current_user=current_user,
        class_id=data.class_id,
        siswa_id=data.siswa_id,
        label=LABEL,
    )
    _reject_pengganti(data.invoice_kind)
    _validate_tax_period(data.invoice_date, data.tax_month, data.tax_year)
    _validate_identity(
        data.buyer_identity_type, data.buyer_identity_number, data.buyer_document_number
    )
    payload = data.model_dump(exclude={"class_id", "siswa_id", "lines"})
    invoice = TaxInvoice(
        **payload,
        tenant_id=tenant_id,
        class_id=class_id,
        siswa_id=siswa_id,
        created_by_id=current_user.id,
    )
    session.add(invoice)
    session.flush()

    for order, line_data in enumerate(data.lines):
        session.add(build_line(line_data, invoice_id=invoice.id, line_order=order))
    session.flush()

    recalculate_invoice(session, invoice)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Terjadi benturan data, silakan coba lagi",
        ) from exc
    session.refresh(invoice)
    return invoice_to_read(session, invoice)


@router.post("/bulk-issue", response_model=TaxInvoiceBulkIssueResult, dependencies=[_roles])
def bulk_issue(
    data: TaxInvoiceBulkIssue, current_user: CurrentUser, session: SessionDep
) -> TaxInvoiceBulkIssueResult:
    """Upload Faktur — issue many drafts at once (partial success).

    Each id is processed independently: an invalid or inaccessible id, a
    non-draft, a draft without lines, or a numbering conflict only marks that
    item as failed while the rest still go through. The session is rolled back
    after any per-item error so a bad row can never poison the remainder.
    """
    result = TaxInvoiceBulkIssueResult()
    for invoice_id in data.ids:
        try:
            invoice = session.get(TaxInvoice, invoice_id)
            if invoice is None or not can_read_invoice(session, current_user, invoice):
                result.skipped += 1
                result.errors.append(f"#{invoice_id}: {LABEL} tidak ditemukan")
                continue
            if invoice.status != TaxInvoiceStatus.draft:
                result.skipped += 1
                result.errors.append(f"#{invoice_id}: bukan draft")
                continue
            if not _invoice_lines(session, invoice.id):
                result.skipped += 1
                result.errors.append(f"#{invoice_id}: belum ada detail transaksi")
                continue
            issue_invoice(session, invoice)
            session.add(invoice)
            session.commit()
            result.issued += 1
        except IntegrityError:
            session.rollback()
            result.skipped += 1
            result.errors.append(f"#{invoice_id}: nomor faktur bentrok, coba lagi")
    return result


@router.get("/{invoice_id}", response_model=TaxInvoiceRead, dependencies=[_roles])
def get_invoice(
    invoice_id: int, current_user: CurrentUser, session: SessionDep
) -> TaxInvoiceRead:
    return invoice_to_read(session, _get_accessible_invoice(invoice_id, current_user, session))


@router.patch("/{invoice_id}", response_model=TaxInvoiceRead, dependencies=[_roles])
def update_invoice(
    invoice_id: int, data: TaxInvoiceUpdate, current_user: CurrentUser, session: SessionDep
) -> TaxInvoiceRead:
    invoice = _get_accessible_invoice(invoice_id, current_user, session)
    _require_draft(invoice, "diubah")

    payload = data.model_dump(exclude_unset=True)
    lines = payload.pop("lines", None)

    tenant_id, resolved_class_id, resolved_siswa_id = resolve_document_scope(
        session=session,
        current_user=current_user,
        class_id=payload.get("class_id", invoice.class_id),
        siswa_id=payload.get("siswa_id", invoice.siswa_id),
        label=LABEL,
    )
    invoice.tenant_id = tenant_id
    invoice.class_id = resolved_class_id
    invoice.siswa_id = resolved_siswa_id

    for field, value in payload.items():
        if field in {"class_id", "siswa_id"}:
            continue
        setattr(invoice, field, value)

    _reject_pengganti(invoice.invoice_kind)
    _validate_tax_period(invoice.invoice_date, invoice.tax_month, invoice.tax_year)
    _validate_identity(
        invoice.buyer_identity_type,
        invoice.buyer_identity_number,
        invoice.buyer_document_number,
    )

    if lines is not None:
        _replace_lines(session, invoice, [TaxInvoiceLineCreate.model_validate(x) for x in lines])

    recalculate_invoice(session, invoice)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Terjadi benturan data, silakan coba lagi",
        ) from exc
    session.refresh(invoice)
    return invoice_to_read(session, invoice)


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[_roles])
def delete_invoice(invoice_id: int, current_user: CurrentUser, session: SessionDep) -> None:
    """Hapus Dokumen — only drafts can be deleted."""
    invoice = _get_accessible_invoice(invoice_id, current_user, session)
    _require_draft(invoice, "dihapus")
    session.delete(invoice)
    session.commit()


@router.post("/{invoice_id}/issue", response_model=TaxInvoiceRead, dependencies=[_roles])
def issue(invoice_id: int, current_user: CurrentUser, session: SessionDep) -> TaxInvoiceRead:
    """Upload Faktur — assigns the Nomor Faktur and moves the draft to Telah Terbit."""
    invoice = _get_accessible_invoice(invoice_id, current_user, session)
    _require_draft(invoice, "diterbitkan")
    if not _invoice_lines(session, invoice.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{LABEL} belum memiliki detail transaksi",
        )
    issue_invoice(session, invoice)
    session.add(invoice)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Nomor faktur bentrok, silakan coba lagi",
        ) from exc
    session.refresh(invoice)
    return invoice_to_read(session, invoice)


@router.post("/{invoice_id}/cancel", response_model=TaxInvoiceRead, dependencies=[_cancel_roles])
def cancel(
    invoice_id: int, data: TaxInvoiceCancel, current_user: CurrentUser, session: SessionDep
) -> TaxInvoiceRead:
    """Batalkan — an issued faktur becomes Tidak Valid."""
    invoice = _get_accessible_invoice(invoice_id, current_user, session)
    if invoice.status != TaxInvoiceStatus.issued:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Hanya {LABEL} terbit yang dapat dibatalkan",
        )
    invoice.status = TaxInvoiceStatus.invalid
    invoice.invoice_kind = TaxInvoiceKind.dibatalkan
    invoice.invalid_reason = data.reason or "Dibatalkan oleh penjual"
    session.add(invoice)
    session.commit()
    session.refresh(invoice)
    return invoice_to_read(session, invoice)


@router.patch("/{invoice_id}/review", response_model=TaxInvoiceRead, dependencies=[_review_roles])
def review(
    invoice_id: int, data: TaxInvoiceReview, current_user: CurrentUser, session: SessionDep
) -> TaxInvoiceRead:
    invoice = _get_accessible_invoice(invoice_id, current_user, session)
    payload = data.model_dump(exclude_unset=True)
    for field, value in payload.items():
        setattr(invoice, field, value)
    session.add(invoice)
    session.commit()
    session.refresh(invoice)
    return invoice_to_read(session, invoice)


# --- Detail Transaksi line endpoints ------------------------------------------


@router.post(
    "/{invoice_id}/lines",
    response_model=TaxInvoiceRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_roles],
)
def add_line(
    invoice_id: int,
    data: TaxInvoiceLineCreate,
    current_user: CurrentUser,
    session: SessionDep,
) -> TaxInvoiceRead:
    invoice = _get_accessible_invoice(invoice_id, current_user, session)
    _require_draft(invoice, "ditambah detail transaksi")
    existing = _invoice_lines(session, invoice.id)
    session.add(build_line(data, invoice_id=invoice.id, line_order=len(existing)))
    session.flush()
    recalculate_invoice(session, invoice)
    session.commit()
    session.refresh(invoice)
    return invoice_to_read(session, invoice)


@router.patch(
    "/{invoice_id}/lines/{line_id}", response_model=TaxInvoiceRead, dependencies=[_roles]
)
def update_line(
    invoice_id: int,
    line_id: int,
    data: TaxInvoiceLineUpdate,
    current_user: CurrentUser,
    session: SessionDep,
) -> TaxInvoiceRead:
    invoice = _get_accessible_invoice(invoice_id, current_user, session)
    _require_draft(invoice, "diubah detail transaksinya")

    line = session.get(TaxInvoiceLine, line_id)
    if line is None or line.invoice_id != invoice.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Detail transaksi tidak ditemukan"
        )

    payload = data.model_dump(exclude_unset=True)
    if "quantity" in payload:
        line.quantity_milli = quantity_to_milli(payload.pop("quantity"))
    if "ppn_rate_percent" in payload:
        line.ppn_rate_basis_points = percent_to_basis_points(payload.pop("ppn_rate_percent"))
    if "ppnbm_rate_percent" in payload:
        line.ppnbm_rate_basis_points = percent_to_basis_points(payload.pop("ppnbm_rate_percent"))
    for field, value in payload.items():
        setattr(line, field, value)

    session.add(line)
    session.flush()
    recalculate_invoice(session, invoice)
    session.commit()
    session.refresh(invoice)
    return invoice_to_read(session, invoice)


@router.delete(
    "/{invoice_id}/lines/{line_id}", response_model=TaxInvoiceRead, dependencies=[_roles]
)
def delete_line(
    invoice_id: int, line_id: int, current_user: CurrentUser, session: SessionDep
) -> TaxInvoiceRead:
    invoice = _get_accessible_invoice(invoice_id, current_user, session)
    _require_draft(invoice, "dihapus detail transaksinya")

    line = session.get(TaxInvoiceLine, line_id)
    if line is None or line.invoice_id != invoice.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Detail transaksi tidak ditemukan"
        )
    session.delete(line)
    session.flush()
    recalculate_invoice(session, invoice)
    session.commit()
    session.refresh(invoice)
    return invoice_to_read(session, invoice)
