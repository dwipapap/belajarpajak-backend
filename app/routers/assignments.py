"""Tugas (assignments), auto-grade submissions, teacher grading, and score exports."""

from __future__ import annotations

import csv
import io
import re
from datetime import UTC, datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlmodel import Session, select

from app.core.deps import CurrentUser, SessionDep, require_roles
from app.models.assignment import Assignment, AssignmentSubmission
from app.models.enrollment import Enrollment
from app.models.enums import (
    AssignmentDocType,
    AssignmentStatus,
    Role,
)
from app.models.school_class import SchoolClass
from app.models.slip import WithholdingSlip
from app.models.tax_input_invoice import TaxInputInvoice
from app.models.tax_invoice import TaxInvoice
from app.models.tax_return import TaxReturn
from app.models.user import User
from app.schemas.assignment import (
    AssignmentCreate,
    AssignmentDetailRead,
    AssignmentRead,
    AssignmentUpdate,
    AutoCheckRead,
    CandidateDocRead,
    SubmissionGradeRequest,
    SubmissionRead,
    SubmissionSubmitRequest,
)
from app.services.audit import log_action
from app.services.auto_grade import SLIP_DOC_TYPES, SPT_DOC_TYPES, auto_grade_document

router = APIRouter(prefix="/tugas", tags=["tugas"])

_all_roles = Depends(require_roles(Role.superadmin, Role.admin, Role.guru, Role.siswa))
_guru_only = Depends(require_roles(Role.guru))
_guru_admin = Depends(require_roles(Role.superadmin, Role.admin, Role.guru))
_siswa_only = Depends(require_roles(Role.siswa))

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ---------------------------------------------------------------------------
# Helpers: Document Resolution & Access
# ---------------------------------------------------------------------------


def _resolve_document(
    session: Session, doc_type: AssignmentDocType | str, doc_id: int
) -> Any | None:
    """Fetch the underlying document row matching ``doc_type`` and ``doc_id``."""
    dtype = AssignmentDocType(doc_type)
    if dtype in SLIP_DOC_TYPES:
        slip = session.get(WithholdingSlip, doc_id)
        if slip is not None and slip.slip_type == SLIP_DOC_TYPES[dtype]:
            return slip
        return None
    if dtype == AssignmentDocType.faktur_keluaran:
        return session.get(TaxInvoice, doc_id)
    if dtype == AssignmentDocType.faktur_masukan:
        return session.get(TaxInputInvoice, doc_id)
    if dtype in SPT_DOC_TYPES:
        tr = session.get(TaxReturn, doc_id)
        if tr is not None and tr.return_type in SPT_DOC_TYPES[dtype]:
            return tr
        return None
    return None


def _doc_metadata(
    doc_type: AssignmentDocType | str, doc: Any | None
) -> tuple[str | None, str | None, str | None, int | None]:
    """Extract (doc_number, doc_status, doc_period, doc_tax_amount) from a document."""
    if doc is None:
        return None, None, None, None
    dtype = AssignmentDocType(doc_type)
    period = f"{doc.tax_month:02d}/{doc.tax_year}"
    status_val = doc.status.value if hasattr(doc.status, "value") else str(doc.status)
    if dtype in SLIP_DOC_TYPES:
        return doc.withholding_number, status_val, period, int(doc.income_tax)
    if dtype in (AssignmentDocType.faktur_keluaran, AssignmentDocType.faktur_masukan):
        return doc.invoice_number, status_val, period, int(doc.total_ppn)
    if dtype in SPT_DOC_TYPES:
        return doc.bpe_number, status_val, period, int(doc.amount_due)
    return None, status_val, period, None


def _list_student_candidate_docs(
    session: Session,
    *,
    tenant_id: int,
    siswa_id: int,
    doc_type: AssignmentDocType | str,
) -> list[CandidateDocRead]:
    """List all documents owned by ``siswa_id`` matching ``doc_type``."""
    dtype = AssignmentDocType(doc_type)
    out: list[CandidateDocRead] = []
    if dtype in SLIP_DOC_TYPES:
        slips = session.exec(
            select(WithholdingSlip)
            .where(
                WithholdingSlip.tenant_id == tenant_id,
                WithholdingSlip.siswa_id == siswa_id,
                WithholdingSlip.slip_type == SLIP_DOC_TYPES[dtype],
            )
            .order_by(WithholdingSlip.id.desc())
        ).all()
        for s in slips:
            out.append(
                CandidateDocRead(
                    id=s.id,
                    doc_type=dtype,
                    number=s.withholding_number,
                    status=s.status.value,
                    tax_month=s.tax_month,
                    tax_year=s.tax_year,
                    counterparty_name=s.recipient_name or s.withholder_name,
                    dpp_amount=int(s.dpp),
                    tax_amount=int(s.income_tax),
                    created_at=s.created_at,
                )
            )
    elif dtype == AssignmentDocType.faktur_keluaran:
        invoices = session.exec(
            select(TaxInvoice)
            .where(
                TaxInvoice.tenant_id == tenant_id,
                TaxInvoice.siswa_id == siswa_id,
            )
            .order_by(TaxInvoice.id.desc())
        ).all()
        for inv in invoices:
            out.append(
                CandidateDocRead(
                    id=inv.id,
                    doc_type=dtype,
                    number=inv.invoice_number,
                    status=inv.status.value,
                    tax_month=inv.tax_month,
                    tax_year=inv.tax_year,
                    counterparty_name=inv.buyer_name,
                    dpp_amount=int(inv.total_dpp),
                    tax_amount=int(inv.total_ppn),
                    created_at=inv.created_at,
                )
            )
    elif dtype == AssignmentDocType.faktur_masukan:
        inputs = session.exec(
            select(TaxInputInvoice)
            .where(
                TaxInputInvoice.tenant_id == tenant_id,
                TaxInputInvoice.siswa_id == siswa_id,
            )
            .order_by(TaxInputInvoice.id.desc())
        ).all()
        for inp in inputs:
            out.append(
                CandidateDocRead(
                    id=inp.id,
                    doc_type=dtype,
                    number=inp.invoice_number,
                    status=inp.status.value,
                    tax_month=inp.tax_month,
                    tax_year=inp.tax_year,
                    counterparty_name=inp.seller_name,
                    dpp_amount=int(inp.total_dpp),
                    tax_amount=int(inp.total_ppn),
                    created_at=inp.created_at,
                )
            )
    elif dtype in SPT_DOC_TYPES:
        returns = session.exec(
            select(TaxReturn)
            .where(
                TaxReturn.tenant_id == tenant_id,
                TaxReturn.siswa_id == siswa_id,
                TaxReturn.return_type.in_(SPT_DOC_TYPES[dtype]),
            )
            .order_by(TaxReturn.id.desc())
        ).all()
        for tr in returns:
            dpp_amt = (
                int(tr.penyerahan_dpp)
                if tr.return_type == SPT_DOC_TYPES[AssignmentDocType.spt_ppn][0]
                else int(tr.total_income_tax)
            )
            out.append(
                CandidateDocRead(
                    id=tr.id,
                    doc_type=dtype,
                    number=tr.bpe_number,
                    status=tr.status.value,
                    tax_month=tr.tax_month,
                    tax_year=tr.tax_year,
                    counterparty_name=f"Masa {tr.tax_month:02d}/{tr.tax_year}",
                    dpp_amount=dpp_amt,
                    tax_amount=int(tr.amount_due),
                    created_at=tr.created_at,
                )
            )
    return out


def _enrolled_siswa(session: Session, class_id: int) -> list[User]:
    """Return all active/enrolled siswa users in ``class_id`` sorted by full_name."""
    rows = session.exec(
        select(User)
        .join(Enrollment, Enrollment.siswa_id == User.id)
        .where(Enrollment.class_id == class_id, User.role == Role.siswa)
        .order_by(User.full_name, User.id)
    ).all()
    return list(rows)


def _is_student_enrolled(session: Session, class_id: int, siswa_id: int) -> bool:
    enr = session.exec(
        select(Enrollment).where(
            Enrollment.class_id == class_id,
            Enrollment.siswa_id == siswa_id,
        )
    ).first()
    return enr is not None


def _can_access_assignment(
    session: Session, user: User, assignment: Assignment
) -> bool:
    if user.role == Role.superadmin:
        return True
    if user.role == Role.admin:
        return assignment.tenant_id == user.tenant_id
    if user.role == Role.guru:
        if assignment.tenant_id != user.tenant_id:
            return False
        sc = session.get(SchoolClass, assignment.class_id)
        return sc is not None and sc.guru_id == user.id
    if user.role == Role.siswa:
        if assignment.tenant_id != user.tenant_id:
            return False
        if assignment.status == AssignmentStatus.draft:
            return False
        return _is_student_enrolled(session, assignment.class_id, user.id)
    return False


def _require_guru_owned_assignment(
    session: Session, user: User, assignment_id: int
) -> Assignment:
    assignment = session.get(Assignment, assignment_id)
    if assignment is None or not _can_access_assignment(session, user, assignment):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tugas tidak ditemukan"
        )
    if user.role != Role.guru:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Hanya guru pengampu kelas yang dapat mengubah tugas ini",
        )
    return assignment


def _build_submission_read(
    session: Session,
    assignment: Assignment,
    siswa: User,
    sub: AssignmentSubmission | None,
    *,
    hide_auto_checks: bool = False,
) -> SubmissionRead:
    nis = siswa.username or siswa.email
    if sub is None:
        return SubmissionRead(
            id=None,
            assignment_id=assignment.id,
            siswa_id=siswa.id,
            siswa_name=siswa.full_name,
            siswa_nis=nis,
            siswa_email=siswa.email,
            submitted=False,
        )
    doc = _resolve_document(session, assignment.doc_type, sub.doc_id)
    doc_number, doc_status, doc_period, doc_tax_amount = _doc_metadata(
        assignment.doc_type, doc
    )
    displayed = sub.final_score if sub.final_score is not None else sub.auto_score
    if hide_auto_checks:
        checks: list[AutoCheckRead] = []
    else:
        checks = [AutoCheckRead.model_validate(c) for c in (sub.auto_checks or [])]
    return SubmissionRead(
        id=sub.id,
        assignment_id=assignment.id,
        siswa_id=siswa.id,
        siswa_name=siswa.full_name,
        siswa_nis=nis,
        siswa_email=siswa.email,
        submitted=True,
        doc_id=sub.doc_id,
        doc_number=doc_number,
        doc_status=doc_status,
        doc_period=doc_period,
        doc_tax_amount=doc_tax_amount,
        submitted_at=sub.submitted_at,
        is_late=sub.is_late,
        auto_score=sub.auto_score,
        auto_checks=checks,
        auto_checks_hidden=hide_auto_checks,
        final_score=sub.final_score,
        displayed_score=displayed,
        teacher_feedback=sub.teacher_feedback,
        graded_by=sub.graded_by,
        graded_at=sub.graded_at,
    )


def _should_hide_auto_checks(session: Session, user: User) -> bool:
    from app.core.deps import get_user_tenant_settings

    if user.role != Role.siswa:
        return False
    settings = get_user_tenant_settings(session, user)
    return not settings.get("show_auto_check_details", True)


def _build_assignment_read(
    session: Session, assignment: Assignment, current_user: User
) -> AssignmentRead:
    sc = session.get(SchoolClass, assignment.class_id)
    creator = session.get(User, assignment.created_by)
    enrolled_students = _enrolled_siswa(session, assignment.class_id)
    subs = session.exec(
        select(AssignmentSubmission).where(
            AssignmentSubmission.assignment_id == assignment.id
        )
    ).all()
    sub_by_siswa = {s.siswa_id: s for s in subs}

    my_sub_read: SubmissionRead | None = None
    if current_user.role == Role.siswa:
        my_sub = sub_by_siswa.get(current_user.id)
        hide = _should_hide_auto_checks(session, current_user)
        my_sub_read = _build_submission_read(
            session, assignment, current_user, my_sub, hide_auto_checks=hide
        )

    return AssignmentRead(
        id=assignment.id,
        tenant_id=assignment.tenant_id,
        class_id=assignment.class_id,
        class_name=sc.name if sc else "-",
        created_by=assignment.created_by,
        creator_name=creator.full_name if creator else "-",
        title=assignment.title,
        instructions=assignment.instructions,
        doc_type=AssignmentDocType(assignment.doc_type),
        due_at=assignment.due_at,
        status=assignment.status,
        created_at=assignment.created_at,
        updated_at=assignment.updated_at,
        enrolled_count=len(enrolled_students),
        submitted_count=len(subs),
        graded_count=sum(1 for s in subs if s.final_score is not None),
        my_submission=my_sub_read,
    )


def _build_assignment_detail(
    session: Session, assignment: Assignment, current_user: User
) -> AssignmentDetailRead:
    base = _build_assignment_read(session, assignment, current_user)
    submissions_list: list[SubmissionRead] = []
    if current_user.role in (Role.guru, Role.admin, Role.superadmin):
        enrolled_students = _enrolled_siswa(session, assignment.class_id)
        subs = session.exec(
            select(AssignmentSubmission).where(
                AssignmentSubmission.assignment_id == assignment.id
            )
        ).all()
        sub_by_siswa = {s.siswa_id: s for s in subs}
        for siswa in enrolled_students:
            submissions_list.append(
                _build_submission_read(
                    session, assignment, siswa, sub_by_siswa.get(siswa.id)
                )
            )
    return AssignmentDetailRead(
        **base.model_dump(),
        submissions=submissions_list,
    )


def _compute_is_late(due_at: datetime | None, now: datetime) -> bool:
    if due_at is None:
        return False
    due = due_at if due_at.tzinfo is not None else due_at.replace(tzinfo=UTC)
    return now > due


# ---------------------------------------------------------------------------
# Endpoints: CRUD & Lifecycle
# ---------------------------------------------------------------------------


@router.get("", response_model=list[AssignmentRead], dependencies=[_all_roles])
def list_assignments(
    current_user: CurrentUser,
    session: SessionDep,
    class_id: int | None = None,
    status_filter: Annotated[AssignmentStatus | None, Query(alias="status")] = None,
    doc_type: AssignmentDocType | None = None,
    tenant_id: int | None = None,
) -> list[AssignmentRead]:
    from app.core.deps import get_user_effective_modules
    from app.core.modules import enabled_assignment_doc_types

    stmt = select(Assignment)

    if current_user.role == Role.superadmin:
        if tenant_id is not None:
            stmt = stmt.where(Assignment.tenant_id == tenant_id)
    elif current_user.role == Role.admin:
        stmt = stmt.where(Assignment.tenant_id == current_user.tenant_id)
    elif current_user.role == Role.guru:
        taught_ids = select(SchoolClass.id).where(
            SchoolClass.tenant_id == current_user.tenant_id,
            SchoolClass.guru_id == current_user.id,
        )
        stmt = stmt.where(
            Assignment.tenant_id == current_user.tenant_id,
            Assignment.class_id.in_(taught_ids),
        )
    elif current_user.role == Role.siswa:
        allowed_doc_types = enabled_assignment_doc_types(
            get_user_effective_modules(session, current_user)
        )
        if not allowed_doc_types:
            return []
        enrolled_class_ids = select(Enrollment.class_id).where(
            Enrollment.siswa_id == current_user.id
        )
        stmt = stmt.where(
            Assignment.tenant_id == current_user.tenant_id,
            Assignment.class_id.in_(enrolled_class_ids),
            Assignment.status.in_(
                [AssignmentStatus.published, AssignmentStatus.closed]
            ),
            Assignment.doc_type.in_(allowed_doc_types),
        )

    if class_id is not None:
        stmt = stmt.where(Assignment.class_id == class_id)
    if status_filter is not None:
        stmt = stmt.where(Assignment.status == status_filter)
    if doc_type is not None:
        stmt = stmt.where(Assignment.doc_type == doc_type.value)

    stmt = stmt.order_by(Assignment.id.desc())
    rows = session.exec(stmt).all()
    return [_build_assignment_read(session, a, current_user) for a in rows]


@router.post(
    "",
    response_model=AssignmentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_guru_only],
)
def create_assignment(
    data: AssignmentCreate,
    current_user: CurrentUser,
    session: SessionDep,
) -> AssignmentRead:
    from app.core.deps import ensure_module_enabled
    from app.core.modules import assignment_doc_type_to_module

    ensure_module_enabled(
        session, current_user, assignment_doc_type_to_module(data.doc_type)
    )
    sc = session.get(SchoolClass, data.class_id)
    if (
        sc is None
        or sc.tenant_id != current_user.tenant_id
        or sc.guru_id != current_user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kelas tidak ditemukan atau bukan kelas yang Anda ampu",
        )

    initial_status = (
        AssignmentStatus.published if data.publish else AssignmentStatus.draft
    )
    assignment = Assignment(
        tenant_id=current_user.tenant_id,
        class_id=data.class_id,
        created_by=current_user.id,
        title=data.title.strip(),
        instructions=data.instructions,
        doc_type=data.doc_type.value,
        due_at=data.due_at,
        status=initial_status,
    )
    session.add(assignment)
    session.flush()

    log_action(
        session,
        actor=current_user,
        action="create",
        entity="assignment",
        entity_id=assignment.id,
        tenant_id=assignment.tenant_id,
        summary=(
            f"Buat tugas '{assignment.title}' ({assignment.doc_type}) "
            f"untuk kelas #{assignment.class_id}"
        ),
    )
    if data.publish:
        log_action(
            session,
            actor=current_user,
            action="publish",
            entity="assignment",
            entity_id=assignment.id,
            tenant_id=assignment.tenant_id,
            summary=f"Publikasikan tugas '{assignment.title}'",
        )
    session.commit()
    session.refresh(assignment)
    return _build_assignment_read(session, assignment, current_user)



# ---------------------------------------------------------------------------
# Export Rekap Kelas (placed before /{assignment_id} so static path matches)
# ---------------------------------------------------------------------------


def _safe_filename(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_\-]+", "-", text.strip()).strip("-")
    return cleaned or "tugas"


def _cell(value: Any) -> Any:
    """Neutralise text that a spreadsheet would evaluate as a formula."""
    if isinstance(value, str) and len(value) > 1 and value[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _render_xlsx(sheet_title: str, headers: list[str], rows: list[list[Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title[:31]

    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.freeze_panes = "A2"
    ws.print_title_rows = "1:1"

    header_fill = PatternFill(start_color="1E3A5F", end_color="1E3A5F", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF", size=11)
    thin = Side(border_style="thin", color="D1D5DB")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws.append(headers)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border

    for r_idx, row_vals in enumerate(rows, start=2):
        ws.append([_cell(v) for v in row_vals])
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=r_idx, column=col_idx)
            cell.border = border
            cell.alignment = Alignment(vertical="center")

    for col_idx in range(1, len(headers) + 1):
        col_letter = get_column_letter(col_idx)
        max_len = max(
            len(str(ws.cell(row=r, column=col_idx).value or ""))
            for r in range(1, len(rows) + 2)
        )
        ws.column_dimensions[col_letter].width = min(max(max_len + 4, 12), 45)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _render_csv(headers: list[str], rows: list[list[Any]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for r in rows:
        writer.writerow(["" if v is None else _cell(v) for v in r])
    return buf.getvalue().encode("utf-8-sig")


@router.get(
    "/rekap-kelas/{class_id}/export",
    dependencies=[_guru_admin],
)
def export_class_rekap(
    class_id: int,
    current_user: CurrentUser,
    session: SessionDep,
    export_format: Annotated[str, Query(alias="format")] = "xlsx",
) -> Response:
    sc = session.get(SchoolClass, class_id)
    if sc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Kelas tidak ditemukan"
        )
    if current_user.role == Role.admin and sc.tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Kelas tidak ditemukan"
        )
    if current_user.role == Role.guru and (
        sc.tenant_id != current_user.tenant_id or sc.guru_id != current_user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kelas tidak ditemukan atau bukan kelas yang Anda ampu",
        )

    assignments = list(
        session.exec(
            select(Assignment)
            .where(
                Assignment.class_id == class_id,
                Assignment.status != AssignmentStatus.draft,
            )
            .order_by(Assignment.id.asc())
        ).all()
    )
    enrolled_students = _enrolled_siswa(session, class_id)

    assignment_ids = [a.id for a in assignments]
    subs = (
        list(
            session.exec(
                select(AssignmentSubmission).where(
                    AssignmentSubmission.assignment_id.in_(assignment_ids)
                )
            ).all()
        )
        if assignment_ids
        else []
    )
    sub_map: dict[tuple[int, int], AssignmentSubmission] = {
        (s.assignment_id, s.siswa_id): s for s in subs
    }

    headers = [
        "Nama",
        "NIS",
        *[f"{a.title} ({a.doc_type.upper()})" for a in assignments],
        "Rata-rata",
    ]
    rows: list[list[Any]] = []
    for siswa in enrolled_students:
        nis = siswa.username or siswa.email
        scores: list[int] = []
        row: list[Any] = [siswa.full_name, nis]
        for a in assignments:
            sub = sub_map.get((a.id, siswa.id))
            if sub is None:
                row.append(None)
                scores.append(0)
            else:
                val = (
                    sub.final_score
                    if sub.final_score is not None
                    else sub.auto_score
                )
                row.append(val)
                scores.append(int(val))
        avg = round(sum(scores) / len(assignments), 2) if assignments else 0.0
        row.append(avg)
        rows.append(row)

    fmt = export_format.lower().strip()
    slug = _safe_filename(sc.name)
    if fmt == "csv":
        data = _render_csv(headers, rows)
        return Response(
            content=data,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="Rekap-Nilai-{slug}.csv"'
            },
        )
    if fmt == "xlsx":
        data = _render_xlsx(f"Rekap {sc.name}", headers, rows)
        return Response(
            content=data,
            media_type=XLSX_MEDIA_TYPE,
            headers={
                "Content-Disposition": f'attachment; filename="Rekap-Nilai-{slug}.xlsx"'
            },
        )
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="Format ekspor harus 'xlsx' atau 'csv'",
    )


# ---------------------------------------------------------------------------
# Endpoints: Single Assignment, Update, Publish, Close, Delete
# ---------------------------------------------------------------------------


@router.get(
    "/{assignment_id}",
    response_model=AssignmentDetailRead,
    dependencies=[_all_roles],
)
def get_assignment(
    assignment_id: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> AssignmentDetailRead:
    from app.core.deps import ensure_module_enabled
    from app.core.modules import assignment_doc_type_to_module

    assignment = session.get(Assignment, assignment_id)
    if assignment is None or not _can_access_assignment(
        session, current_user, assignment
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tugas tidak ditemukan"
        )
    if current_user.role == Role.siswa:
        ensure_module_enabled(
            session, current_user, assignment_doc_type_to_module(assignment.doc_type)
        )
    return _build_assignment_detail(session, assignment, current_user)


@router.patch(
    "/{assignment_id}",
    response_model=AssignmentRead,
    dependencies=[_guru_only],
)
def update_assignment(
    assignment_id: int,
    data: AssignmentUpdate,
    current_user: CurrentUser,
    session: SessionDep,
) -> AssignmentRead:
    from app.core.deps import ensure_module_enabled
    from app.core.modules import assignment_doc_type_to_module

    assignment = _require_guru_owned_assignment(session, current_user, assignment_id)

    payload = data.model_dump(exclude_unset=True)
    if "class_id" in payload and payload["class_id"] is not None:
        new_class_id = payload["class_id"]
        if new_class_id != assignment.class_id:
            has_subs = session.exec(
                select(AssignmentSubmission).where(
                    AssignmentSubmission.assignment_id == assignment.id
                )
            ).first()
            if has_subs is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Kelas tidak dapat diubah karena sudah ada pengumpulan siswa",
                )
            sc = session.get(SchoolClass, new_class_id)
            if (
                sc is None
                or sc.tenant_id != current_user.tenant_id
                or sc.guru_id != current_user.id
            ):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Kelas tujuan tidak ditemukan atau bukan kelas yang Anda ampu",
                )
            assignment.class_id = new_class_id

    if "doc_type" in payload and payload["doc_type"] is not None:
        ensure_module_enabled(
            session, current_user, assignment_doc_type_to_module(payload["doc_type"])
        )
        new_dtype = payload["doc_type"].value
        if new_dtype != assignment.doc_type:
            has_subs = session.exec(
                select(AssignmentSubmission).where(
                    AssignmentSubmission.assignment_id == assignment.id
                )
            ).first()
            if has_subs is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Tipe dokumen tidak dapat diubah karena sudah ada pengumpulan siswa",
                )
            assignment.doc_type = new_dtype

    if "title" in payload and payload["title"] is not None:
        assignment.title = payload["title"].strip()
    if "instructions" in payload and payload["instructions"] is not None:
        assignment.instructions = payload["instructions"]
    if "due_at" in payload:
        assignment.due_at = payload["due_at"]

    assignment.updated_at = datetime.now(UTC)
    session.add(assignment)
    session.commit()
    session.refresh(assignment)
    return _build_assignment_read(session, assignment, current_user)


@router.post(
    "/{assignment_id}/publish",
    response_model=AssignmentRead,
    dependencies=[_guru_only],
)
def publish_assignment(
    assignment_id: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> AssignmentRead:
    assignment = _require_guru_owned_assignment(session, current_user, assignment_id)
    assignment.status = AssignmentStatus.published
    assignment.updated_at = datetime.now(UTC)
    session.add(assignment)
    log_action(
        session,
        actor=current_user,
        action="publish",
        entity="assignment",
        entity_id=assignment.id,
        tenant_id=assignment.tenant_id,
        summary=f"Publikasikan tugas '{assignment.title}'",
    )
    session.commit()
    session.refresh(assignment)
    return _build_assignment_read(session, assignment, current_user)


@router.post(
    "/{assignment_id}/close",
    response_model=AssignmentRead,
    dependencies=[_guru_only],
)
def close_assignment(
    assignment_id: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> AssignmentRead:
    assignment = _require_guru_owned_assignment(session, current_user, assignment_id)
    assignment.status = AssignmentStatus.closed
    assignment.updated_at = datetime.now(UTC)
    session.add(assignment)
    log_action(
        session,
        actor=current_user,
        action="close",
        entity="assignment",
        entity_id=assignment.id,
        tenant_id=assignment.tenant_id,
        summary=f"Tutup tugas '{assignment.title}'",
    )
    session.commit()
    session.refresh(assignment)
    return _build_assignment_read(session, assignment, current_user)


@router.delete(
    "/{assignment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[_guru_only],
)
def delete_assignment(
    assignment_id: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> None:
    assignment = _require_guru_owned_assignment(session, current_user, assignment_id)
    has_sub = session.exec(
        select(AssignmentSubmission).where(
            AssignmentSubmission.assignment_id == assignment.id
        )
    ).first()
    if has_sub is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tugas tidak dapat dihapus karena sudah ada pengumpulan siswa",
        )
    session.delete(assignment)
    session.commit()


# ---------------------------------------------------------------------------
# Endpoints: Submissions, Candidate Docs, Student Submit, Guru Grade
# ---------------------------------------------------------------------------


@router.get(
    "/{assignment_id}/submissions",
    response_model=list[SubmissionRead],
    dependencies=[_guru_admin],
)
def list_assignment_submissions(
    assignment_id: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> list[SubmissionRead]:
    assignment = session.get(Assignment, assignment_id)
    if assignment is None or not _can_access_assignment(
        session, current_user, assignment
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tugas tidak ditemukan"
        )
    detail = _build_assignment_detail(session, assignment, current_user)
    return detail.submissions


@router.get(
    "/{assignment_id}/candidates",
    response_model=list[CandidateDocRead],
    dependencies=[_siswa_only],
)
def list_candidate_documents(
    assignment_id: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> list[CandidateDocRead]:
    from app.core.deps import ensure_module_enabled
    from app.core.modules import assignment_doc_type_to_module

    assignment = session.get(Assignment, assignment_id)
    if assignment is None or not _can_access_assignment(
        session, current_user, assignment
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tugas tidak ditemukan"
        )
    ensure_module_enabled(
        session, current_user, assignment_doc_type_to_module(assignment.doc_type)
    )
    return _list_student_candidate_docs(
        session,
        tenant_id=current_user.tenant_id,
        siswa_id=current_user.id,
        doc_type=assignment.doc_type,
    )


@router.post(
    "/{assignment_id}/submit",
    response_model=SubmissionRead,
    dependencies=[_siswa_only],
)
def submit_assignment(
    assignment_id: int,
    data: SubmissionSubmitRequest,
    current_user: CurrentUser,
    session: SessionDep,
) -> SubmissionRead:
    from app.core.deps import ensure_module_enabled, get_user_tenant_settings
    from app.core.modules import assignment_doc_type_to_module

    assignment = session.get(Assignment, assignment_id)
    if assignment is None or not _can_access_assignment(
        session, current_user, assignment
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tugas tidak ditemukan"
        )
    ensure_module_enabled(
        session, current_user, assignment_doc_type_to_module(assignment.doc_type)
    )
    if assignment.status == AssignmentStatus.closed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tugas sudah ditutup dan tidak lagi menerima pengumpulan",
        )
    if assignment.status != AssignmentStatus.published:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tugas belum dipublikasikan",
        )

    settings = get_user_tenant_settings(session, current_user)
    existing = session.exec(
        select(AssignmentSubmission).where(
            AssignmentSubmission.assignment_id == assignment.id,
            AssignmentSubmission.siswa_id == current_user.id,
        )
    ).first()
    if existing is not None and existing.final_score is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pengumpulan sudah dinilai oleh guru dan tidak dapat dikirim ulang",
        )
    if existing is not None and not settings.get("allow_resubmission", True):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pengiriman ulang tugas tidak diizinkan untuk instansi Anda",
        )

    now = datetime.now(UTC)
    is_late = _compute_is_late(assignment.due_at, now)
    if is_late and not settings.get("allow_late_submission", True):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tenggat sudah lewat",
        )

    doc = _resolve_document(session, assignment.doc_type, data.doc_id)
    if (
        doc is None
        or doc.tenant_id != current_user.tenant_id
        or doc.siswa_id != current_user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dokumen tidak ditemukan atau bukan milik Anda untuk tipe tugas ini",
        )

    auto_score, auto_checks = auto_grade_document(session, assignment.doc_type, doc)

    if existing is None:
        submission = AssignmentSubmission(
            assignment_id=assignment.id,
            siswa_id=current_user.id,
            doc_id=data.doc_id,
            submitted_at=now,
            is_late=is_late,
            auto_score=auto_score,
            auto_checks=auto_checks,
        )
        session.add(submission)
    else:
        existing.doc_id = data.doc_id
        existing.submitted_at = now
        existing.is_late = is_late
        existing.auto_score = auto_score
        existing.auto_checks = auto_checks
        existing.updated_at = now
        submission = existing
        session.add(submission)

    session.flush()
    log_action(
        session,
        actor=current_user,
        action="submit",
        entity="assignment_submission",
        entity_id=submission.id,
        tenant_id=assignment.tenant_id,
        summary=(
            f"Kumpulkan dokumen #{data.doc_id} untuk tugas #{assignment.id} "
            f"(skor otomatis: {auto_score})"
        ),
    )
    session.commit()
    session.refresh(submission)
    hide = not settings.get("show_auto_check_details", True)
    return _build_submission_read(
        session, assignment, current_user, submission, hide_auto_checks=hide
    )



@router.patch(
    "/{assignment_id}/submissions/{siswa_id}",
    response_model=SubmissionRead,
    dependencies=[_guru_only],
)
def grade_submission(
    assignment_id: int,
    siswa_id: int,
    data: SubmissionGradeRequest,
    current_user: CurrentUser,
    session: SessionDep,
) -> SubmissionRead:
    assignment = _require_guru_owned_assignment(session, current_user, assignment_id)
    submission = session.exec(
        select(AssignmentSubmission).where(
            AssignmentSubmission.assignment_id == assignment.id,
            AssignmentSubmission.siswa_id == siswa_id,
        )
    ).first()
    if submission is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pengumpulan siswa belum ditemukan",
        )

    siswa = session.get(User, submission.siswa_id)
    if siswa is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Siswa tidak ditemukan"
        )

    now = datetime.now(UTC)
    submission.final_score = data.final_score
    submission.teacher_feedback = data.teacher_feedback
    submission.graded_by = current_user.id
    submission.graded_at = now
    submission.updated_at = now
    session.add(submission)

    # Sync score and teacher_feedback onto the underlying document so existing
    # document tables & dashboards remain consistent.
    doc = _resolve_document(session, assignment.doc_type, submission.doc_id)
    if doc is not None:
        doc.score = data.final_score
        doc.teacher_feedback = data.teacher_feedback
        session.add(doc)

    log_action(
        session,
        actor=current_user,
        action="grade",
        entity="assignment_submission",
        entity_id=submission.id,
        tenant_id=assignment.tenant_id,
        summary=(
            f"Nilai pengumpulan siswa #{submission.siswa_id} "
            f"pada tugas #{assignment.id}: {data.final_score}"
        ),
    )
    session.commit()
    session.refresh(submission)
    return _build_submission_read(session, assignment, siswa, submission)


# ---------------------------------------------------------------------------
# Feature 3: Export Nilai per Tugas (Excel .xlsx + CSV)
# ---------------------------------------------------------------------------


@router.get(
    "/{assignment_id}/export",
    dependencies=[_guru_admin],
)
def export_assignment_scores(
    assignment_id: int,
    current_user: CurrentUser,
    session: SessionDep,
    export_format: Annotated[str, Query(alias="format")] = "xlsx",
) -> Response:
    assignment = session.get(Assignment, assignment_id)
    if assignment is None or not _can_access_assignment(
        session, current_user, assignment
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tugas tidak ditemukan"
        )

    detail = _build_assignment_detail(session, assignment, current_user)
    headers = [
        "Nama",
        "NIS",
        "Status",
        "Waktu kumpul",
        "Terlambat",
        "Nilai otomatis",
        "Nilai akhir",
        "Umpan balik",
    ]
    rows: list[list[Any]] = []
    for sub in detail.submissions:
        if not sub.submitted:
            rows.append(
                [
                    sub.siswa_name,
                    sub.siswa_nis or sub.siswa_email,
                    "Belum mengumpulkan",
                    "-",
                    "-",
                    None,
                    None,
                    "",
                ]
            )
        else:
            status_label = (
                "Sudah dinilai" if sub.final_score is not None else "Sudah mengumpulkan"
            )
            waktu = (
                sub.submitted_at.astimezone(ZoneInfo("Asia/Jakarta")).strftime("%Y-%m-%d %H:%M WIB")
                if sub.submitted_at
                else "-"
            )
            terlambat = "Ya" if sub.is_late else "Tidak"
            rows.append(
                [
                    sub.siswa_name,
                    sub.siswa_nis or sub.siswa_email,
                    status_label,
                    waktu,
                    terlambat,
                    sub.auto_score,
                    sub.displayed_score,
                    sub.teacher_feedback or "",
                ]
            )

    fmt = export_format.lower().strip()
    slug = _safe_filename(assignment.title)
    if fmt == "csv":
        data = _render_csv(headers, rows)
        return Response(
            content=data,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="Nilai-Tugas-{slug}.csv"'
            },
        )
    if fmt == "xlsx":
        data = _render_xlsx(f"Nilai {assignment.title}", headers, rows)
        return Response(
            content=data,
            media_type=XLSX_MEDIA_TYPE,
            headers={
                "Content-Disposition": f'attachment; filename="Nilai-Tugas-{slug}.xlsx"'
            },
        )
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="Format ekspor harus 'xlsx' atau 'csv'",
    )
