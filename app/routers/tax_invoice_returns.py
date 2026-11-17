"""Retur Pajak Masukan (buyer) and Retur Pajak Keluaran (seller, read-only)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.core.money import basis_points_to_percent
from app.models.enums import Role, TaxInputCreditStatus, TaxInvoiceStatus
from app.models.school_class import SchoolClass
from app.models.tax_input_invoice import TaxInputInvoice, TaxInputLine
from app.models.tax_invoice import TaxInvoice
from app.models.tax_invoice_return import TaxInvoiceReturn, TaxInvoiceReturnLine
from app.models.user import User
from app.routers.tax_input_invoices import can_read_input_invoice
from app.schemas.tax_invoice_return import (
    TaxReturnCreate,
    TaxReturnIssue,
    TaxReturnLineInput,
    TaxReturnLineRead,
    TaxReturnListResponse,
    TaxReturnRead,
    TaxReturnUpdate,
)
from app.services.audit import log_action
from app.services.faktur_retur import already_returned_milli, compute_return_line
from app.services.faktur_status import StatusFaktur
from app.services.signing import require_kode_otorisasi

LABEL = "Retur Pajak Masukan"

router = APIRouter(prefix="/retur-pajak-masukan", tags=["retur-pajak-masukan"])
keluaran_router = APIRouter(prefix="/retur-pajak-keluaran", tags=["retur-pajak-keluaran"])

_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru, Role.siswa))
_write_roles = Depends(require_roles(Role.admin, Role.guru, Role.siswa))

_STATUS_TO_DISPLAY = {
    TaxInvoiceStatus.draft: StatusFaktur.CREATED,
    TaxInvoiceStatus.issued: StatusFaktur.APPROVED,
    TaxInvoiceStatus.invalid: StatusFaktur.CANCELED,
}
_DISPLAY_TO_STATUS = {v: k for k, v in _STATUS_TO_DISPLAY.items()}


def _to_milli(quantity: Decimal) -> int:
    return int((quantity * Decimal("1000")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _milli_to_float(value: int) -> float:
    return float((Decimal(value) / Decimal("1000")).quantize(Decimal("0.001")))


# --- reading -----------------------------------------------------------------


def _return_lines(session: Session, return_id: int) -> list[TaxInvoiceReturnLine]:
    return list(
        session.exec(
            select(TaxInvoiceReturnLine)
            .where(TaxInvoiceReturnLine.return_id == return_id)
            .order_by(TaxInvoiceReturnLine.line_order, TaxInvoiceReturnLine.id)
        ).all()
    )


def to_read(session: Session, ret: TaxInvoiceReturn) -> TaxReturnRead:
    invoice = session.get(TaxInputInvoice, ret.input_invoice_id)
    lines: list[TaxReturnLineRead] = []
    for row in _return_lines(session, ret.id):
        original = session.get(TaxInputLine, row.input_line_id)
        returned = already_returned_milli(session, original.id, exclude_return_id=ret.id)
        lines.append(
            TaxReturnLineRead(
                id=row.id,
                input_line_id=original.id,
                line_order=row.line_order,
                line_type=original.line_type,
                item_code=original.item_code,
                item_name=original.item_name,
                unit=original.unit,
                unit_price=original.unit_price,
                original_quantity=_milli_to_float(original.quantity_milli),
                original_discount=original.discount,
                ppn_rate_percent=basis_points_to_percent(original.ppn_rate_basis_points),
                ppnbm_rate_percent=basis_points_to_percent(original.ppnbm_rate_basis_points),
                use_dpp_other=original.use_dpp_other,
                already_returned_quantity=_milli_to_float(returned),
                max_return_quantity=_milli_to_float(max(original.quantity_milli - returned, 0)),
                return_quantity=_milli_to_float(row.return_quantity_milli),
                return_discount=row.return_discount,
                return_total_price=row.return_total_price,
                dpp=row.dpp,
                dpp_other=row.dpp_other,
                ppn=row.ppn,
                ppnbm=row.ppnbm,
            )
        )
    return TaxReturnRead(
        id=ret.id,
        tenant_id=ret.tenant_id,
        class_id=ret.class_id,
        siswa_id=ret.siswa_id,
        created_by_id=ret.created_by_id,
        input_invoice_id=ret.input_invoice_id,
        status=ret.status,
        status_faktur=_STATUS_TO_DISPLAY[ret.status],
        return_number=ret.return_number,
        return_date=ret.return_date,
        tax_month=ret.tax_month,
        tax_year=ret.tax_year,
        is_creditable=ret.is_creditable,
        issued_at=ret.issued_at,
        electronic_signature_status=ret.electronic_signature_status,
        signer_name=ret.signer_name,
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        transaction_code=invoice.transaction_code,
        seller_npwp=invoice.seller_npwp,
        seller_name=invoice.seller_name,
        buyer_identity_type=invoice.buyer_identity_type,
        buyer_identity_number=invoice.buyer_identity_number,
        buyer_name=invoice.buyer_name,
        credit_status=invoice.credit_status.value,
        total_dpp=ret.total_dpp,
        total_dpp_other=ret.total_dpp_other,
        total_ppn=ret.total_ppn,
        total_ppnbm=ret.total_ppnbm,
        lines=lines,
    )


# --- access ------------------------------------------------------------------


def _can_read_return(session: Session, user: User, ret: TaxInvoiceReturn) -> bool:
    if user.role == Role.superadmin:
        return True
    if user.role == Role.admin:
        return ret.tenant_id == user.tenant_id
    if user.role == Role.siswa:
        return ret.siswa_id == user.id
    if user.role == Role.guru and ret.class_id is not None:
        school_class = session.get(SchoolClass, ret.class_id)
        return school_class is not None and school_class.guru_id == user.id
    return False


def _masukan_filter(query, user: User):
    if user.role == Role.superadmin:
        return query
    if user.role == Role.admin:
        return query.where(TaxInvoiceReturn.tenant_id == user.tenant_id)
    if user.role == Role.siswa:
        return query.where(TaxInvoiceReturn.siswa_id == user.id)
    taught = select(SchoolClass.id).where(SchoolClass.guru_id == user.id)
    return query.where(TaxInvoiceReturn.class_id.in_(taught))


def _get_return(return_id: int, user: User, session: Session) -> TaxInvoiceReturn:
    ret = session.get(TaxInvoiceReturn, return_id)
    if ret is None or not _can_read_return(session, user, ret):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{LABEL} tidak ditemukan")
    return ret


def _require_draft(ret: TaxInvoiceReturn, action: str) -> None:
    if ret.status != TaxInvoiceStatus.draft:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"Hanya {LABEL} draft yang dapat {action}"
        )


# --- validation / calculation --------------------------------------------------


def _validate_date(return_date, invoice: TaxInputInvoice) -> None:
    if return_date < invoice.invoice_date:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Tanggal retur tidak boleh lebih awal dari tanggal faktur pajak",
        )


def _replace_lines(
    session: Session,
    ret: TaxInvoiceReturn,
    invoice: TaxInputInvoice,
    inputs: list[TaxReturnLineInput],
) -> None:
    """Validate returned quantities against the original, rebuild lines and totals."""
    originals = {
        line.id: line
        for line in session.exec(
            select(TaxInputLine).where(TaxInputLine.invoice_id == invoice.id)
        ).all()
    }
    seen: set[int] = set()
    built: list[TaxInvoiceReturnLine] = []
    for order, item in enumerate(inputs):
        original = originals.get(item.input_line_id)
        if original is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Detail transaksi bukan bagian dari faktur pajak yang diretur",
            )
        if item.input_line_id in seen:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Detail transaksi dobel"
            )
        seen.add(item.input_line_id)
        quantity_milli = _to_milli(item.return_quantity)
        available = original.quantity_milli - already_returned_milli(
            session, original.id, exclude_return_id=ret.id
        )
        if quantity_milli > available:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"Jumlah retur '{original.item_name}' melebihi sisa yang dapat diretur "
                    f"({_milli_to_float(max(available, 0))})"
                ),
            )
        if item.return_discount > original.discount:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Diskon retur '{original.item_name}' melebihi diskon faktur",
            )
        amounts = compute_return_line(
            original, quantity_milli=quantity_milli, discount=item.return_discount
        )
        built.append(
            TaxInvoiceReturnLine(
                return_id=ret.id,
                input_line_id=original.id,
                line_order=order,
                return_quantity_milli=quantity_milli,
                return_discount=item.return_discount,
                **amounts,
            )
        )
    if not any(line.return_quantity_milli > 0 for line in built):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Isi jumlah retur pada minimal satu detail transaksi",
        )
    for existing in _return_lines(session, ret.id):
        session.delete(existing)
    session.flush()
    for line in built:
        session.add(line)
    ret.total_dpp = sum(line.dpp for line in built)
    ret.total_dpp_other = sum(line.dpp_other for line in built)
    ret.total_ppn = sum(line.ppn for line in built)
    ret.total_ppnbm = sum(line.ppnbm for line in built)
    session.add(ret)


def _check_number_free(
    session: Session, ret_tenant: int, siswa_id: int, number: str, exclude_id: int | None
) -> None:
    query = select(TaxInvoiceReturn.id).where(
        TaxInvoiceReturn.tenant_id == ret_tenant,
        TaxInvoiceReturn.siswa_id == siswa_id,
        TaxInvoiceReturn.return_number == number,
    )
    if exclude_id is not None:
        query = query.where(TaxInvoiceReturn.id != exclude_id)
    if session.exec(query).first() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Nomor retur sudah dipakai")


def _get_returnable_invoice(session: Session, user: User, invoice_id: int) -> TaxInputInvoice:
    invoice = session.get(TaxInputInvoice, invoice_id)
    if invoice is None or not can_read_input_invoice(session, user, invoice):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="Faktur Pajak Masukan tidak ditemukan"
        )
    # Manual masukan can be credited/uncredited while still a draft; the manual only
    # requires a Kredit/Tidak Kredit status, so either an issued or a decided one passes.
    decided = invoice.credit_status != TaxInputCreditStatus.not_credited
    if invoice.status == TaxInvoiceStatus.invalid or (
        invoice.status != TaxInvoiceStatus.issued and not decided
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                "Retur hanya dapat dibuat untuk faktur masukan yang sudah terbit "
                "(APPROVED/CREDITED)"
            ),
        )
    return invoice


# --- Retur Pajak Masukan endpoints --------------------------------------------------


@router.get("", response_model=TaxReturnListResponse, dependencies=[_roles])
def list_returns(
    current_user: CurrentUser,
    session: SessionDep,
    status_faktur: Annotated[list[StatusFaktur] | None, Query()] = None,
    tax_year: Annotated[int | None, Query(ge=2020, le=2100)] = None,
    tax_month: Annotated[int | None, Query(ge=1, le=12)] = None,
    seller_name: str | None = None,
    invoice_number: str | None = None,
    input_invoice_id: int | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TaxReturnListResponse:
    conditions = []
    if status_faktur:
        wanted = [_DISPLAY_TO_STATUS[s] for s in status_faktur if s in _DISPLAY_TO_STATUS]
        conditions.append(TaxInvoiceReturn.status.in_(wanted) if wanted else False)
    if tax_year is not None:
        conditions.append(TaxInvoiceReturn.tax_year == tax_year)
    if tax_month is not None:
        conditions.append(TaxInvoiceReturn.tax_month == tax_month)
    if input_invoice_id is not None:
        conditions.append(TaxInvoiceReturn.input_invoice_id == input_invoice_id)
    base = select(TaxInvoiceReturn).join(
        TaxInputInvoice, TaxInputInvoice.id == TaxInvoiceReturn.input_invoice_id
    )
    if seller_name:
        conditions.append(TaxInputInvoice.seller_name.ilike(f"%{seller_name}%"))
    if invoice_number:
        conditions.append(TaxInputInvoice.invoice_number.ilike(f"%{invoice_number}%"))
    total = session.exec(
        _masukan_filter(
            select(func.count())
            .select_from(TaxInvoiceReturn)
            .join(TaxInputInvoice, TaxInputInvoice.id == TaxInvoiceReturn.input_invoice_id)
            .where(*conditions),
            current_user,
        )
    ).one()
    rows = session.exec(
        _masukan_filter(
            base.where(*conditions)
            .order_by(TaxInvoiceReturn.id.desc())
            .offset((page - 1) * size)
            .limit(size),
            current_user,
        )
    ).all()
    return TaxReturnListResponse(
        items=[to_read(session, row) for row in rows], total=total, page=page, size=size
    )


@router.post(
    "",
    response_model=TaxReturnRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_write_roles],
)
def create_return(
    data: TaxReturnCreate, current_user: CurrentUser, session: SessionDep
) -> TaxReturnRead:
    invoice = _get_returnable_invoice(session, current_user, data.input_invoice_id)
    _validate_date(data.return_date, invoice)
    number = data.return_number.strip()
    _check_number_free(session, invoice.tenant_id, invoice.siswa_id, number, None)
    ret = TaxInvoiceReturn(
        tenant_id=invoice.tenant_id,
        class_id=invoice.class_id,
        siswa_id=invoice.siswa_id,
        created_by_id=current_user.id,
        input_invoice_id=invoice.id,
        return_number=number,
        return_date=data.return_date,
        tax_month=data.return_date.month,
        tax_year=data.return_date.year,
        is_creditable=(
            data.is_creditable
            if data.is_creditable is not None
            else invoice.credit_status == TaxInputCreditStatus.credited
        ),
    )
    session.add(ret)
    session.flush()
    _replace_lines(session, ret, invoice, data.lines)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Nomor retur sudah dipakai") from exc
    log_action(
        session,
        actor=current_user,
        action="document.create",
        entity="retur_pajak_masukan",
        entity_id=ret.id,
        tenant_id=ret.tenant_id,
        summary=f"Membuat konsep retur {ret.return_number} atas faktur {invoice.invoice_number}",
    )
    session.commit()
    session.refresh(ret)
    return to_read(session, ret)


@router.get("/{return_id}", response_model=TaxReturnRead, dependencies=[_roles])
def get_return(return_id: int, current_user: CurrentUser, session: SessionDep) -> TaxReturnRead:
    return to_read(session, _get_return(return_id, current_user, session))


@router.patch("/{return_id}", response_model=TaxReturnRead, dependencies=[_write_roles])
def update_return(
    return_id: int, data: TaxReturnUpdate, current_user: CurrentUser, session: SessionDep
) -> TaxReturnRead:
    ret = _get_return(return_id, current_user, session)
    _require_draft(ret, "diubah")
    invoice = session.get(TaxInputInvoice, ret.input_invoice_id)
    if data.return_date is not None:
        _validate_date(data.return_date, invoice)
        ret.return_date = data.return_date
        ret.tax_month = data.return_date.month
        ret.tax_year = data.return_date.year
    if data.return_number is not None:
        number = data.return_number.strip()
        _check_number_free(session, ret.tenant_id, ret.siswa_id, number, ret.id)
        ret.return_number = number
    if data.is_creditable is not None:
        ret.is_creditable = data.is_creditable
    session.add(ret)
    if data.lines is not None:
        _replace_lines(session, ret, invoice, data.lines)
    session.commit()
    session.refresh(ret)
    return to_read(session, ret)


@router.delete("/{return_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[_write_roles])
def delete_return(return_id: int, current_user: CurrentUser, session: SessionDep) -> None:
    ret = _get_return(return_id, current_user, session)
    _require_draft(ret, "dihapus")
    session.delete(ret)
    session.commit()


@router.post("/{return_id}/issue", response_model=TaxReturnRead, dependencies=[_write_roles])
def issue_return(
    return_id: int, data: TaxReturnIssue, current_user: CurrentUser, session: SessionDep
) -> TaxReturnRead:
    """Submit/TTE — signs the draft and makes it visible to the seller."""
    ret = _get_return(return_id, current_user, session)
    _require_draft(ret, "dikirim")
    require_kode_otorisasi(data.kode_otorisasi)
    invoice = session.get(TaxInputInvoice, ret.input_invoice_id)
    if invoice.status == TaxInvoiceStatus.invalid:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Faktur Pajak Masukan sudah tidak berlaku"
        )
    # Quantities may have been consumed by another retur since the draft was saved.
    stored = _return_lines(session, ret.id)
    _replace_lines(
        session,
        ret,
        invoice,
        [
            TaxReturnLineInput(
                input_line_id=line.input_line_id,
                return_quantity=Decimal(line.return_quantity_milli) / Decimal("1000"),
                return_discount=line.return_discount,
            )
            for line in stored
        ],
    )
    ret.status = TaxInvoiceStatus.issued
    ret.issued_at = datetime.now(UTC)
    ret.electronic_signature_status = "signed"
    ret.signer_id = current_user.id
    ret.signer_name = data.signer_name or current_user.full_name
    session.add(ret)
    session.commit()
    log_action(
        session,
        actor=current_user,
        action="document.issue",
        entity="retur_pajak_masukan",
        entity_id=ret.id,
        tenant_id=ret.tenant_id,
        summary=f"Mengirim retur {ret.return_number} atas faktur {invoice.invoice_number}",
    )
    session.commit()
    session.refresh(ret)
    return to_read(session, ret)


# --- Retur Pajak Keluaran (seller view, read-only) ------------------------------------


def _keluaran_scope(query, user: User):
    """Issued returns whose masukan copy originated from the user's keluaran."""
    query = query.where(TaxInvoiceReturn.status == TaxInvoiceStatus.issued)
    if user.role == Role.superadmin:
        return query
    if user.role == Role.admin:
        return query.where(TaxInvoice.tenant_id == user.tenant_id)
    if user.role == Role.siswa:
        return query.where(TaxInvoice.siswa_id == user.id)
    taught = select(SchoolClass.id).where(SchoolClass.guru_id == user.id)
    return query.where(TaxInvoice.class_id.in_(taught))


def _keluaran_join(query):
    return query.join(
        TaxInputInvoice, TaxInputInvoice.id == TaxInvoiceReturn.input_invoice_id
    ).join(TaxInvoice, TaxInvoice.id == TaxInputInvoice.source_invoice_id)


@keluaran_router.get("", response_model=TaxReturnListResponse, dependencies=[_roles])
def list_keluaran_returns(
    current_user: CurrentUser,
    session: SessionDep,
    tax_year: Annotated[int | None, Query(ge=2020, le=2100)] = None,
    tax_month: Annotated[int | None, Query(ge=1, le=12)] = None,
    buyer_name: str | None = None,
    invoice_number: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TaxReturnListResponse:
    conditions = []
    if tax_year is not None:
        conditions.append(TaxInvoiceReturn.tax_year == tax_year)
    if tax_month is not None:
        conditions.append(TaxInvoiceReturn.tax_month == tax_month)
    if buyer_name:
        conditions.append(TaxInputInvoice.buyer_name.ilike(f"%{buyer_name}%"))
    if invoice_number:
        conditions.append(TaxInputInvoice.invoice_number.ilike(f"%{invoice_number}%"))
    total = session.exec(
        _keluaran_scope(
            _keluaran_join(select(func.count()).select_from(TaxInvoiceReturn)).where(*conditions),
            current_user,
        )
    ).one()
    rows = session.exec(
        _keluaran_scope(
            _keluaran_join(select(TaxInvoiceReturn)).where(*conditions),
            current_user,
        )
        .order_by(TaxInvoiceReturn.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    return TaxReturnListResponse(
        items=[to_read(session, row) for row in rows], total=total, page=page, size=size
    )


@keluaran_router.get("/{return_id}", response_model=TaxReturnRead, dependencies=[_roles])
def get_keluaran_return(
    return_id: int, current_user: CurrentUser, session: SessionDep
) -> TaxReturnRead:
    ret = session.exec(
        _keluaran_scope(
            _keluaran_join(select(TaxInvoiceReturn)).where(TaxInvoiceReturn.id == return_id),
            current_user,
        )
    ).first()
    if ret is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="Retur Pajak Keluaran tidak ditemukan"
        )
    return to_read(session, ret)
