"""Simulated e-Faktur Pajak Masukan routes.

Mirrors Pajak Keluaran but without NSFP generation — the invoice_number is
copied from the source keluaran (or a manual dummy). Kreditkan/Batal Kredit
toggles credit_status orthogonally to the draft/issued/invalid lifecycle.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.core.money import apply_basis_points, basis_points_to_percent, percent_to_basis_points
from app.core.scope import resolve_document_scope
from app.models.enrollment import Enrollment
from app.models.enums import (
    BuyerIdentityType,
    Role,
    TaxInputCreditStatus,
    TaxInvoiceKind,
    TaxInvoiceStatus,
)
from app.models.school_class import SchoolClass
from app.models.tax_input_invoice import TaxInputInvoice, TaxInputLine
from app.models.tax_invoice import TaxInvoice, TaxInvoiceLine
from app.models.user import User
from app.schemas.tax_input_invoice import (
    DPP_OTHER_CODES,
    TaxInputBulkCredit,
    TaxInputBulkCreditResult,
    TaxInputBulkDelete,
    TaxInputBulkDeleteResult,
    TaxInputBulkUncredit,
    TaxInputBulkUncreditResult,
    TaxInputCancel,
    TaxInputCreate,
    TaxInputLineCreate,
    TaxInputLineRead,
    TaxInputLineUpdate,
    TaxInputListResponse,
    TaxInputRead,
    TaxInputReview,
    TaxInputSummary,
    TaxInputUpdate,
)

LABEL = "Faktur Pajak Masukan"
MAX_EXPORT_ROWS = 5_000

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
    "Kredit",
    "NPWP Penjual",
    "Nama Penjual",
    "NPWP Pembeli / Identitas lainnya",
    "Nama Pembeli",
    "Harga Jual/DPP (Rp)",
    "DPP Nilai Lain (Rp)",
    "PPN (Rp)",
    "PPnBM (Rp)",
)

router = APIRouter(prefix="/faktur-masukan", tags=["faktur-masukan"])

_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru, Role.siswa))
_review_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru))
_credit_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.siswa))


# --- tax engine ---------------------------------------------------------------


def quantity_to_milli(quantity: Decimal) -> int:
    value = quantity * Decimal("1000")
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def milli_to_quantity(quantity_milli: int) -> float:
    return float((Decimal(quantity_milli) / Decimal("1000")).quantize(Decimal("0.001")))


def dpp_nilai_lain(dpp: int) -> int:
    value = Decimal(dpp) * DPP_OTHER_NUMERATOR / DPP_OTHER_DENOMINATOR
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def calculate_line(line: TaxInputLine) -> None:
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


def recalculate_input_invoice(session: Session, invoice: TaxInputInvoice) -> None:
    """Recompute every line and roll the results up into the header totals."""
    lines = _input_lines(session, invoice.id)
    forced = invoice.transaction_code in DPP_OTHER_CODES
    for line in lines:
        # For 04/05 DPP Nilai Lain is forced; otherwise keep user's flag.
        # Do not persist forced True when switching away from 04/05 — the stored
        # flag should reflect user intent, not the previous forced state.
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


def build_input_line(data: TaxInputLineCreate, *, invoice_id: int, line_order: int) -> TaxInputLine:
    return TaxInputLine(
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


# --- access helpers -----------------------------------------------------------


def can_read_input_invoice(session: Session, current_user: User, invoice: TaxInputInvoice) -> bool:
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


def can_read_keluaran_for_source(session: Session, current_user: User, source: TaxInvoice) -> bool:
    # Prepopulated: siswa may clone keluaran teman sekelas (same tenant + same class) even if not owner.
    if current_user.role == Role.superadmin:
        return True
    if current_user.role == Role.admin:
        return source.tenant_id == current_user.tenant_id
    if current_user.role == Role.siswa:
        if source.tenant_id != current_user.tenant_id:
            return False
        if source.siswa_id == current_user.id:
            return True
        if source.class_id is not None:
            enrolled = session.exec(
                select(Enrollment).where(
                    Enrollment.class_id == source.class_id, Enrollment.siswa_id == current_user.id
                )
            ).first()
            return enrolled is not None
        return False
    if current_user.role == Role.guru and source.class_id is not None:
        school_class = session.get(SchoolClass, source.class_id)
        return school_class is not None and school_class.guru_id == current_user.id
    return source.tenant_id == current_user.tenant_id


def apply_access_filters(query, current_user: User):
    if current_user.role == Role.superadmin:
        return query
    if current_user.role == Role.admin:
        return query.where(TaxInputInvoice.tenant_id == current_user.tenant_id)
    if current_user.role == Role.siswa:
        return query.where(TaxInputInvoice.siswa_id == current_user.id)

    taught_class_ids = select(SchoolClass.id).where(SchoolClass.guru_id == current_user.id)
    return query.where(TaxInputInvoice.class_id.in_(taught_class_ids))


def _input_lines(session: Session, invoice_id: int | None) -> list[TaxInputLine]:
    if invoice_id is None:
        return []
    return list(
        session.exec(
            select(TaxInputLine)
            .where(TaxInputLine.invoice_id == invoice_id)
            .order_by(TaxInputLine.line_order, TaxInputLine.id)
        ).all()
    )


def line_to_read(line: TaxInputLine) -> TaxInputLineRead:
    return TaxInputLineRead.model_validate(
        {
            **line.model_dump(),
            "quantity": milli_to_quantity(line.quantity_milli),
            "ppn_rate_percent": basis_points_to_percent(line.ppn_rate_basis_points),
            "ppnbm_rate_percent": basis_points_to_percent(line.ppnbm_rate_basis_points),
        }
    )


def invoice_to_read(session: Session, invoice: TaxInputInvoice) -> TaxInputRead:
    return TaxInputRead.model_validate(
        {
            **invoice.model_dump(),
            "lines": [line_to_read(line) for line in _input_lines(session, invoice.id)],
        }
    )


def _get_accessible_input_invoice(
    invoice_id: int, current_user: CurrentUser, session: SessionDep
) -> TaxInputInvoice:
    invoice = session.get(TaxInputInvoice, invoice_id)
    if invoice is None or not can_read_input_invoice(session, current_user, invoice):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{LABEL} tidak ditemukan")
    return invoice


def _require_draft(invoice: TaxInputInvoice, action: str) -> None:
    if invoice.status != TaxInvoiceStatus.draft:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Hanya {LABEL} draft yang dapat {action}",
        )


def _require_not_credited_for_mutation(invoice: TaxInputInvoice) -> None:
    if invoice.credit_status == TaxInputCreditStatus.credited:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{LABEL} yang sudah dikreditkan tidak dapat diubah/dihapus",
        )


def _validate_tax_period(invoice_date: date, tax_month: int, tax_year: int) -> None:
    if invoice_date.month != tax_month or invoice_date.year != tax_year:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Tanggal faktur harus sesuai dengan masa pajak yang dipilih",
        )


def _reject_pengganti(kind: TaxInvoiceKind) -> None:
    if kind == TaxInvoiceKind.pengganti:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Jenis faktur pengganti belum didukung",
        )


def _validate_identity(
    identity_type: BuyerIdentityType,
    identity_number: str | None,
    document_number: str | None,
) -> None:
    number = (identity_number or "").strip()
    doc = (document_number or "").strip()

    if identity_type == BuyerIdentityType.nik:
        if not number.isdigit() or len(number) != 16:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="NIK harus 16 digit angka",
            )
    elif identity_type == BuyerIdentityType.npwp:
        cleaned = number.replace(".", "").replace("-", "")
        if not cleaned or not cleaned.isdigit() or not (15 <= len(cleaned) <= 16):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Nomor NPWP tidak valid",
            )
    else:  # paspor / identitas_lain
        if not number:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Nomor identitas wajib diisi untuk paspor/identitas lain",
            )
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Nomor dokumen wajib diisi untuk paspor/identitas lain",
            )


def _check_credit_permission(invoice: TaxInputInvoice, current_user: User) -> None:
    # Guru never allowed to credit/uncredit, only review
    if current_user.role == Role.guru:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Guru tidak dapat mengkreditkan faktur")
    if current_user.role == Role.siswa and invoice.siswa_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tidak dapat mengkreditkan milik orang lain")
    if current_user.role == Role.admin and invoice.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Akses tenant ditolak")


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _sanitize_csv_value(value: str) -> str:
    if value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _input_conditions(
    *,
    status: TaxInvoiceStatus | None = None,
    credit_status: TaxInputCreditStatus | None = None,
    tenant_id: int | None = None,
    class_id: int | None = None,
    siswa_id: int | None = None,
    tax_year: int | None = None,
    tax_month: int | None = None,
    transaction_code: str | None = None,
    seller_name: str | None = None,
    buyer_name: str | None = None,
    invoice_number: str | None = None,
    current_user: User,
) -> list:
    conditions = []
    if status is not None:
        conditions.append(TaxInputInvoice.status == status)
    if credit_status is not None:
        conditions.append(TaxInputInvoice.credit_status == credit_status)
    if tax_year is not None:
        conditions.append(TaxInputInvoice.tax_year == tax_year)
    if tax_month is not None:
        conditions.append(TaxInputInvoice.tax_month == tax_month)
    if transaction_code is not None:
        conditions.append(TaxInputInvoice.transaction_code == transaction_code)
    if seller_name:
        conditions.append(TaxInputInvoice.seller_name.ilike(f"%{_escape_like(seller_name)}%", escape="\\"))
    if buyer_name:
        conditions.append(TaxInputInvoice.buyer_name.ilike(f"%{_escape_like(buyer_name)}%", escape="\\"))
    if invoice_number:
        conditions.append(TaxInputInvoice.invoice_number.ilike(f"%{_escape_like(invoice_number)}%", escape="\\"))
    if current_user.role == Role.superadmin and tenant_id is not None:
        conditions.append(TaxInputInvoice.tenant_id == tenant_id)
    if class_id is not None:
        conditions.append(TaxInputInvoice.class_id == class_id)
    if siswa_id is not None and current_user.role != Role.siswa:
        conditions.append(TaxInputInvoice.siswa_id == siswa_id)
    return conditions


# --- endpoints ----------------------------------------------------------------


@router.get("", response_model=TaxInputListResponse, dependencies=[_roles])
def list_input_invoices(
    current_user: CurrentUser,
    session: SessionDep,
    status_filter: Annotated[TaxInvoiceStatus | None, Query(alias="status")] = None,
    credit_status: TaxInputCreditStatus | None = None,
    tenant_id: int | None = None,
    class_id: int | None = None,
    siswa_id: int | None = None,
    tax_year: Annotated[int | None, Query(ge=2020, le=2100)] = None,
    tax_month: Annotated[int | None, Query(ge=1, le=12)] = None,
    transaction_code: Annotated[str | None, Query(pattern=r"^(0[1-9]|10)$")] = None,
    seller_name: str | None = None,
    buyer_name: str | None = None,
    invoice_number: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TaxInputListResponse:
    conditions = _input_conditions(
        status=status_filter,
        credit_status=credit_status,
        tenant_id=tenant_id,
        class_id=class_id,
        siswa_id=siswa_id,
        tax_year=tax_year,
        tax_month=tax_month,
        transaction_code=transaction_code,
        seller_name=seller_name,
        buyer_name=buyer_name,
        invoice_number=invoice_number,
        current_user=current_user,
    )

    total = session.exec(
        apply_access_filters(
            select(func.count()).select_from(TaxInputInvoice).where(*conditions), current_user
        )
    ).one()
    items = session.exec(
        apply_access_filters(
            select(TaxInputInvoice)
            .where(*conditions)
            .order_by(TaxInputInvoice.id.desc())
            .offset((page - 1) * size)
            .limit(size),
            current_user,
        )
    ).all()
    return TaxInputListResponse(
        items=[invoice_to_read(session, item) for item in items],
        total=total,
        page=page,
        size=size,
    )


@router.get("/summary", response_model=TaxInputSummary, dependencies=[_roles])
def input_summary(current_user: CurrentUser, session: SessionDep) -> TaxInputSummary:
    status_counts = dict(
        session.exec(
            apply_access_filters(
                select(TaxInputInvoice.status, func.count())
                .select_from(TaxInputInvoice)
                .group_by(TaxInputInvoice.status),
                current_user,
            )
        ).all()
    )
    credit_counts = dict(
        session.exec(
            apply_access_filters(
                select(TaxInputInvoice.credit_status, func.count())
                .select_from(TaxInputInvoice)
                .where(TaxInputInvoice.status != TaxInvoiceStatus.invalid)
                .group_by(TaxInputInvoice.credit_status),
                current_user,
            )
        ).all()
    )
    draft = status_counts.get(TaxInvoiceStatus.draft, 0)
    invalid = status_counts.get(TaxInvoiceStatus.invalid, 0)
    credited = credit_counts.get(TaxInputCreditStatus.credited, 0)
    not_credited = credit_counts.get(TaxInputCreditStatus.not_credited, 0)
    return TaxInputSummary(
        draft=draft,
        credited=credited,
        not_credited=not_credited,
        invalid=invalid,
        total=sum(status_counts.values()),
    )


@router.get("/export-csv", dependencies=[_roles])
def export_csv(
    current_user: CurrentUser,
    session: SessionDep,
    status_filter: Annotated[TaxInvoiceStatus | None, Query(alias="status")] = None,
    credit_status: TaxInputCreditStatus | None = None,
    tenant_id: int | None = None,
    class_id: int | None = None,
    siswa_id: int | None = None,
    tax_year: Annotated[int | None, Query(ge=2020, le=2100)] = None,
    tax_month: Annotated[int | None, Query(ge=1, le=12)] = None,
    transaction_code: Annotated[str | None, Query(pattern=r"^(0[1-9]|10)$")] = None,
    seller_name: str | None = None,
    buyer_name: str | None = None,
    invoice_number: str | None = None,
) -> Response:
    conditions = _input_conditions(
        status=status_filter,
        credit_status=credit_status,
        tenant_id=tenant_id,
        class_id=class_id,
        siswa_id=siswa_id,
        tax_year=tax_year,
        tax_month=tax_month,
        transaction_code=transaction_code,
        seller_name=seller_name,
        buyer_name=buyer_name,
        invoice_number=invoice_number,
        current_user=current_user,
    )

    total = session.exec(
        apply_access_filters(
            select(func.count()).select_from(TaxInputInvoice).where(*conditions), current_user
        )
    ).one()
    if total > MAX_EXPORT_ROWS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Terlalu banyak data untuk ekspor. Persempit filter masa pajak atau status.",
        )

    invoices = session.exec(
        apply_access_filters(
            select(TaxInputInvoice).where(*conditions).order_by(TaxInputInvoice.id).limit(MAX_EXPORT_ROWS),
            current_user,
        )
    ).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_CSV_COLUMNS)
    for inv in invoices:
        writer.writerow(
            [
                _sanitize_csv_value(inv.invoice_number or ""),
                inv.invoice_date.isoformat(),
                inv.tax_month,
                inv.tax_year,
                inv.transaction_code,
                inv.invoice_kind.value,
                inv.status.value,
                inv.credit_status.value,
                _sanitize_csv_value(inv.seller_npwp or ""),
                _sanitize_csv_value(inv.seller_name or ""),
                _sanitize_csv_value(inv.buyer_identity_number),
                _sanitize_csv_value(inv.buyer_name),
                inv.total_dpp,
                inv.total_dpp_other,
                inv.total_ppn,
                inv.total_ppnbm,
            ]
        )
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="faktur_masukan_export.csv"'},
    )


@router.post(
    "",
    response_model=TaxInputRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_roles],
)
def create_input_invoice(
    data: TaxInputCreate, current_user: CurrentUser, session: SessionDep
) -> TaxInputRead:
    tenant_id, class_id, siswa_id = resolve_document_scope(
        session=session,
        current_user=current_user,
        class_id=data.class_id,
        siswa_id=data.siswa_id,
        label=LABEL,
    )
    _reject_pengganti(data.invoice_kind)
    _validate_tax_period(data.invoice_date, data.tax_month, data.tax_year)
    _validate_identity(data.buyer_identity_type, data.buyer_identity_number, data.buyer_document_number)

    # Handle source_invoice_id prepopulated
    source: TaxInvoice | None = None
    if data.source_invoice_id is not None:
        source = session.get(TaxInvoice, data.source_invoice_id)
        if source is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Faktur sumber tidak ditemukan")
        if not can_read_keluaran_for_source(session, current_user, source):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Faktur sumber tidak ditemukan")
        if source.tenant_id != tenant_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sumber beda tenant tidak dapat dipakai")
        if source.status != TaxInvoiceStatus.issued:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Hanya faktur sumber terbit yang dapat dikreditkan"
            )

    payload = data.model_dump(exclude={"class_id", "siswa_id", "lines", "source_invoice_id"})
    # If source present, override some fields from source when not explicitly provided
    if source is not None:
        # Copy invoice_number and seller info from source; keep buyer's own identity as target
        if not payload.get("invoice_number"):
            payload["invoice_number"] = source.invoice_number
        if not payload.get("seller_name"):
            payload["seller_name"] = source.seller_name
        if not payload.get("seller_npwp"):
            payload["seller_npwp"] = source.seller_npwp
        if not payload.get("seller_address"):
            payload["seller_address"] = source.seller_address
        if not payload.get("seller_idtku"):
            payload["seller_idtku"] = source.seller_idtku
        # Use source's transaction_code/date etc if not overridden? For latihan, keep payload's own values
        # But ensure invoice_number is copied

    invoice = TaxInputInvoice(
        **payload,
        tenant_id=tenant_id,
        class_id=class_id,
        siswa_id=siswa_id,
        created_by_id=current_user.id,
        source_invoice_id=data.source_invoice_id,
    )
    session.add(invoice)
    session.flush()

    # Build lines
    if data.source_invoice_id is not None and not data.lines:
        # Copy lines from source keluaran
        source_lines = session.exec(
            select(TaxInvoiceLine).where(TaxInvoiceLine.invoice_id == source.id).order_by(TaxInvoiceLine.line_order)
        ).all()
        for order, sline in enumerate(source_lines):
            # Map keluaran line fields to masukan line
            session.add(
                TaxInputLine(
                    invoice_id=invoice.id,
                    line_order=order,
                    line_type=sline.line_type,
                    item_code=sline.item_code,
                    item_name=sline.item_name,
                    unit=sline.unit,
                    unit_price=sline.unit_price,
                    quantity_milli=sline.quantity_milli,
                    discount=sline.discount,
                    use_dpp_other=sline.use_dpp_other,
                    ppn_rate_basis_points=sline.ppn_rate_basis_points,
                    ppnbm_rate_basis_points=sline.ppnbm_rate_basis_points,
                )
            )
    else:
        for order, line_data in enumerate(data.lines):
            session.add(build_input_line(line_data, invoice_id=invoice.id, line_order=order))
    session.flush()

    recalculate_input_invoice(session, invoice)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Terjadi benturan data, silakan coba lagi") from exc
    session.refresh(invoice)
    return invoice_to_read(session, invoice)


@router.post("/bulk-credit", response_model=TaxInputBulkCreditResult, dependencies=[_credit_roles])
def bulk_credit(
    data: TaxInputBulkCredit, current_user: CurrentUser, session: SessionDep
) -> TaxInputBulkCreditResult:
    result = TaxInputBulkCreditResult()
    for invoice_id in data.ids:
        try:
            invoice = session.get(TaxInputInvoice, invoice_id)
            if invoice is None or not can_read_input_invoice(session, current_user, invoice):
                result.skipped += 1
                result.errors.append(f"#{invoice_id}: {LABEL} tidak ditemukan")
                continue
            if invoice.status == TaxInvoiceStatus.invalid:
                result.skipped += 1
                result.errors.append(f"#{invoice_id}: tidak dapat dikreditkan, status tidak valid")
                continue
            if invoice.credit_status == TaxInputCreditStatus.credited:
                result.skipped += 1
                result.errors.append(f"#{invoice_id}: sudah dikreditkan")
                continue
            _check_credit_permission(invoice, current_user)
            invoice.credit_status = TaxInputCreditStatus.credited
            invoice.credited_at = datetime.now(UTC)
            session.add(invoice)
            session.commit()
            result.credited += 1
        except HTTPException as he:
            session.rollback()
            result.skipped += 1
            result.errors.append(f"#{invoice_id}: {he.detail}")
        except IntegrityError:
            session.rollback()
            result.skipped += 1
            result.errors.append(f"#{invoice_id}: gagal kredit, coba lagi")
    return result


@router.post("/bulk-uncredit", response_model=TaxInputBulkUncreditResult, dependencies=[_credit_roles])
def bulk_uncredit(
    data: TaxInputBulkUncredit, current_user: CurrentUser, session: SessionDep
) -> TaxInputBulkUncreditResult:
    result = TaxInputBulkUncreditResult()
    for invoice_id in data.ids:
        try:
            invoice = session.get(TaxInputInvoice, invoice_id)
            if invoice is None or not can_read_input_invoice(session, current_user, invoice):
                result.skipped += 1
                result.errors.append(f"#{invoice_id}: {LABEL} tidak ditemukan")
                continue
            if invoice.credit_status != TaxInputCreditStatus.credited:
                result.skipped += 1
                result.errors.append(f"#{invoice_id}: belum dikreditkan")
                continue
            _check_credit_permission(invoice, current_user)
            invoice.credit_status = TaxInputCreditStatus.not_credited
            invoice.credited_at = None
            session.add(invoice)
            session.commit()
            result.uncredited += 1
        except HTTPException as he:
            session.rollback()
            result.skipped += 1
            result.errors.append(f"#{invoice_id}: {he.detail}")
        except IntegrityError:
            session.rollback()
            result.skipped += 1
            result.errors.append(f"#{invoice_id}: gagal batal kredit, coba lagi")
    return result


@router.post("/bulk-delete", response_model=TaxInputBulkDeleteResult, dependencies=[_roles])
def bulk_delete(
    data: TaxInputBulkDelete, current_user: CurrentUser, session: SessionDep
) -> TaxInputBulkDeleteResult:
    result = TaxInputBulkDeleteResult()
    for invoice_id in data.ids:
        try:
            invoice = session.get(TaxInputInvoice, invoice_id)
            if invoice is None or not can_read_input_invoice(session, current_user, invoice):
                result.skipped += 1
                result.errors.append(f"#{invoice_id}: {LABEL} tidak ditemukan")
                continue
            if invoice.status != TaxInvoiceStatus.draft:
                result.skipped += 1
                result.errors.append(f"#{invoice_id}: hanya draft yang dapat dihapus")
                continue
            if invoice.credit_status == TaxInputCreditStatus.credited:
                result.skipped += 1
                result.errors.append(f"#{invoice_id}: sudah dikreditkan tidak dapat dihapus")
                continue
            session.delete(invoice)
            session.commit()
            result.deleted += 1
        except IntegrityError:
            session.rollback()
            result.skipped += 1
            result.errors.append(f"#{invoice_id}: gagal hapus, coba lagi")
    return result


@router.get("/{invoice_id}", response_model=TaxInputRead, dependencies=[_roles])
def get_input_invoice(
    invoice_id: int, current_user: CurrentUser, session: SessionDep
) -> TaxInputRead:
    return invoice_to_read(session, _get_accessible_input_invoice(invoice_id, current_user, session))


@router.patch("/{invoice_id}", response_model=TaxInputRead, dependencies=[_roles])
def update_input_invoice(
    invoice_id: int, data: TaxInputUpdate, current_user: CurrentUser, session: SessionDep
) -> TaxInputRead:
    invoice = _get_accessible_input_invoice(invoice_id, current_user, session)
    _require_draft(invoice, "diubah")
    _require_not_credited_for_mutation(invoice)

    payload = data.model_dump(exclude_unset=True)
    lines = payload.pop("lines", None)
    old_transaction_code = invoice.transaction_code

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
    _validate_identity(invoice.buyer_identity_type, invoice.buyer_identity_number, invoice.buyer_document_number)

    # Clear forced DPP flag when moving away from 04/05 — stored True was due to force
    if (
        old_transaction_code in DPP_OTHER_CODES
        and invoice.transaction_code not in DPP_OTHER_CODES
        and lines is None
    ):
        for existing in _input_lines(session, invoice.id):
            existing.use_dpp_other = False
            session.add(existing)
        session.flush()

    if lines is not None:
        # Replace lines
        for existing in _input_lines(session, invoice.id):
            session.delete(existing)
        session.flush()
        for order, line_data in enumerate(lines):
            session.add(build_input_line(TaxInputLineCreate.model_validate(line_data), invoice_id=invoice.id, line_order=order))
        session.flush()

    recalculate_input_invoice(session, invoice)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Terjadi benturan data, silakan coba lagi") from exc
    session.refresh(invoice)
    return invoice_to_read(session, invoice)


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[_roles])
def delete_input_invoice(invoice_id: int, current_user: CurrentUser, session: SessionDep) -> None:
    invoice = _get_accessible_input_invoice(invoice_id, current_user, session)
    _require_draft(invoice, "dihapus")
    _require_not_credited_for_mutation(invoice)
    session.delete(invoice)
    session.commit()


@router.post("/{invoice_id}/credit", response_model=TaxInputRead, dependencies=[_credit_roles])
def credit(invoice_id: int, current_user: CurrentUser, session: SessionDep) -> TaxInputRead:
    invoice = _get_accessible_input_invoice(invoice_id, current_user, session)
    if invoice.status == TaxInvoiceStatus.invalid:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{LABEL} tidak valid tidak dapat dikreditkan")
    if invoice.credit_status == TaxInputCreditStatus.credited:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{LABEL} sudah dikreditkan")
    _check_credit_permission(invoice, current_user)
    invoice.credit_status = TaxInputCreditStatus.credited
    invoice.credited_at = datetime.now(UTC)
    session.add(invoice)
    session.commit()
    session.refresh(invoice)
    return invoice_to_read(session, invoice)


@router.post("/{invoice_id}/uncredit", response_model=TaxInputRead, dependencies=[_credit_roles])
def uncredit(invoice_id: int, current_user: CurrentUser, session: SessionDep) -> TaxInputRead:
    invoice = _get_accessible_input_invoice(invoice_id, current_user, session)
    if invoice.credit_status != TaxInputCreditStatus.credited:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{LABEL} belum dikreditkan")
    _check_credit_permission(invoice, current_user)
    invoice.credit_status = TaxInputCreditStatus.not_credited
    invoice.credited_at = None
    session.add(invoice)
    session.commit()
    session.refresh(invoice)
    return invoice_to_read(session, invoice)


@router.post("/{invoice_id}/cancel", response_model=TaxInputRead, dependencies=[Depends(require_roles(Role.superadmin, Role.admin))])
def cancel(
    invoice_id: int, data: TaxInputCancel, current_user: CurrentUser, session: SessionDep
) -> TaxInputRead:
    invoice = _get_accessible_input_invoice(invoice_id, current_user, session)
    if invoice.status == TaxInvoiceStatus.invalid:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{LABEL} sudah tidak valid")
    invoice.status = TaxInvoiceStatus.invalid
    invoice.invoice_kind = TaxInvoiceKind.dibatalkan
    invoice.invalid_reason = data.reason or "Dibatalkan"
    # Reset credit if needed? Keep credit_status but invalid blocks credit
    session.add(invoice)
    session.commit()
    session.refresh(invoice)
    return invoice_to_read(session, invoice)


@router.patch("/{invoice_id}/review", response_model=TaxInputRead, dependencies=[_review_roles])
def review(
    invoice_id: int, data: TaxInputReview, current_user: CurrentUser, session: SessionDep
) -> TaxInputRead:
    invoice = _get_accessible_input_invoice(invoice_id, current_user, session)
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
    response_model=TaxInputRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_roles],
)
def add_line(
    invoice_id: int,
    data: TaxInputLineCreate,
    current_user: CurrentUser,
    session: SessionDep,
) -> TaxInputRead:
    invoice = _get_accessible_input_invoice(invoice_id, current_user, session)
    _require_draft(invoice, "ditambah detail transaksi")
    _require_not_credited_for_mutation(invoice)
    existing = _input_lines(session, invoice.id)
    session.add(build_input_line(data, invoice_id=invoice.id, line_order=len(existing)))
    session.flush()
    recalculate_input_invoice(session, invoice)
    session.commit()
    session.refresh(invoice)
    return invoice_to_read(session, invoice)


@router.patch(
    "/{invoice_id}/lines/{line_id}", response_model=TaxInputRead, dependencies=[_roles]
)
def update_line(
    invoice_id: int,
    line_id: int,
    data: TaxInputLineUpdate,
    current_user: CurrentUser,
    session: SessionDep,
) -> TaxInputRead:
    invoice = _get_accessible_input_invoice(invoice_id, current_user, session)
    _require_draft(invoice, "diubah detail transaksinya")
    _require_not_credited_for_mutation(invoice)

    line = session.get(TaxInputLine, line_id)
    if line is None or line.invoice_id != invoice.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Detail transaksi tidak ditemukan")

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
    recalculate_input_invoice(session, invoice)
    session.commit()
    session.refresh(invoice)
    return invoice_to_read(session, invoice)


@router.delete(
    "/{invoice_id}/lines/{line_id}", response_model=TaxInputRead, dependencies=[_roles]
)
def delete_line(
    invoice_id: int, line_id: int, current_user: CurrentUser, session: SessionDep
) -> TaxInputRead:
    invoice = _get_accessible_input_invoice(invoice_id, current_user, session)
    _require_draft(invoice, "dihapus detail transaksinya")
    _require_not_credited_for_mutation(invoice)

    line = session.get(TaxInputLine, line_id)
    if line is None or line.invoice_id != invoice.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Detail transaksi tidak ditemukan")
    session.delete(line)
    session.flush()
    recalculate_input_invoice(session, invoice)
    session.commit()
    session.refresh(invoice)
    return invoice_to_read(session, invoice)
