"""Dashboard aggregation helpers.

All aggregates use grouped COUNT / MAX queries (one per source table), never
per-student loops, so the cost is constant in the number of students.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from sqlalchemy import and_
from sqlmodel import Session, func, select

from app.models.assignment import Assignment, AssignmentSubmission
from app.models.billing import BillingCode
from app.models.enrollment import Enrollment
from app.models.enums import (
    AssignmentStatus,
    BillingStatus,
    Role,
    SlipStatus,
    TaxInputCreditStatus,
    TaxInvoiceStatus,
    TaxReturnStatus,
)
from app.models.school_class import SchoolClass
from app.models.slip import WithholdingSlip
from app.models.tagihan import Tagihan, TagihanStatus
from app.models.tax_input_invoice import TaxInputInvoice
from app.models.tax_invoice import TaxInvoice
from app.models.tax_return import TaxReturn
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.dashboard import (
    GradingItem,
    GradingModuleCount,
    GuruClassOption,
    GuruDashboard,
    GuruStudentProgress,
    PlatformDashboard,
    QuotaUsage,
    SiswaBillingCounts,
    SiswaDashboard,
    SiswaDocument,
    SiswaGradeStats,
    SiswaMasukanCounts,
    SiswaSptCounts,
    SiswaTodo,
    StatusCounts,
    TenantOverview,
)

MODULE_KELUARAN = "faktur-keluaran"
MODULE_MASUKAN = "faktur-masukan"
MODULE_SPT = "spt"

MODULE_LABELS: dict[str, str] = {
    "bp21": "BP21",
    "bp23": "BP23",
    "bp26": "BP26",
    "bppu": "BPPU",
    "bpmp": "BPMP",
    "bpnr": "BPNR",
    "sp": "SP",
    "cy": "CY",
    MODULE_KELUARAN: "e-Faktur Keluaran",
    MODULE_MASUKAN: "e-Faktur Masukan",
    MODULE_SPT: "SPT Masa",
}

SPT_TYPE_LABELS = {
    "ppn": "SPT Masa PPN",
    "pph_unifikasi": "SPT PPh Unifikasi",
    "pph_21_26": "SPT PPh 21/26",
    "pph21_26": "SPT PPh 21/26",
}

QUEUE_LIMIT = 20


def _period(month: int | None, year: int | None) -> str | None:
    if not month or not year:
        return None
    return f"{month:02d}/{year}"


def _val(x) -> str:
    return getattr(x, "value", x)


def _maxdt(a: datetime | None, b: datetime | None) -> datetime | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def _enabled_slip_types(effective: set[str]) -> list:
    from app.models.enums import SlipType

    out = []
    for st in (
        SlipType.bp21,
        SlipType.bp26,
        SlipType.bppu,
        SlipType.bpmp,
        SlipType.bpnr,
        SlipType.sp,
        SlipType.cy,
    ):
        if st.value in effective:
            out.append(st)
    return out


# --- Guru -----------------------------------------------------------------------


def _guru_grading_conditions(class_id: int, effective: set[str]):
    """(model, extra where) for each gradable source: issued/credited/reported, no score."""
    from app.core.modules import enabled_spt_return_types

    slip_types = _enabled_slip_types(effective)
    spt_types = enabled_spt_return_types(effective)
    return [
        (
            WithholdingSlip,
            and_(
                WithholdingSlip.class_id == class_id,
                WithholdingSlip.status == SlipStatus.issued,
                WithholdingSlip.score.is_(None),
                WithholdingSlip.slip_type.in_(slip_types)
                if slip_types
                else WithholdingSlip.id == -1,
            ),
        ),
        (
            TaxInvoice,
            and_(
                TaxInvoice.class_id == class_id,
                TaxInvoice.status == TaxInvoiceStatus.issued,
                TaxInvoice.score.is_(None),
                *([] if "faktur_keluaran" in effective else [TaxInvoice.id == -1]),
            ),
        ),
        (
            TaxInputInvoice,
            and_(
                TaxInputInvoice.class_id == class_id,
                TaxInputInvoice.credit_status == TaxInputCreditStatus.credited,
                TaxInputInvoice.status != TaxInvoiceStatus.invalid,
                TaxInputInvoice.score.is_(None),
                *([] if "faktur_masukan" in effective else [TaxInputInvoice.id == -1]),
            ),
        ),
        (
            TaxReturn,
            and_(
                TaxReturn.class_id == class_id,
                TaxReturn.status == TaxReturnStatus.reported,
                TaxReturn.score.is_(None),
                TaxReturn.return_type.in_(spt_types)
                if spt_types
                else TaxReturn.id == -1,
            ),
        ),
    ]


def _pending_by_class(
    session: Session, class_ids: list[int], effective: set[str]
) -> dict[int, int]:
    from app.core.modules import enabled_spt_return_types

    result: dict[int, int] = defaultdict(int)
    if not class_ids:
        return result
    slip_types = _enabled_slip_types(effective)
    spt_types = enabled_spt_return_types(effective)

    sources = []
    if slip_types:
        sources.append(
            (
                WithholdingSlip,
                and_(
                    WithholdingSlip.status == SlipStatus.issued,
                    WithholdingSlip.slip_type.in_(slip_types),
                ),
            )
        )
    if "faktur_keluaran" in effective:
        sources.append((TaxInvoice, TaxInvoice.status == TaxInvoiceStatus.issued))
    if "faktur_masukan" in effective:
        sources.append(
            (
                TaxInputInvoice,
                and_(
                    TaxInputInvoice.credit_status == TaxInputCreditStatus.credited,
                    TaxInputInvoice.status != TaxInvoiceStatus.invalid,
                ),
            )
        )
    if spt_types:
        sources.append(
            (
                TaxReturn,
                and_(
                    TaxReturn.status == TaxReturnStatus.reported,
                    TaxReturn.return_type.in_(spt_types),
                ),
            )
        )
    for model, cond in sources:
        rows = session.exec(
            select(model.class_id, func.count())
            .where(model.class_id.in_(class_ids), cond, model.score.is_(None))
            .group_by(model.class_id)
        ).all()
        for cid, n in rows:
            result[cid] += n
    return result


def build_guru_dashboard(
    session: Session, guru: User, class_id: int | None
) -> GuruDashboard | None:
    """Returns None when ``class_id`` is not one of the guru's own classes."""
    from app.core.deps import get_user_effective_modules
    from app.core.modules import enabled_assignment_doc_types, enabled_spt_return_types

    effective = get_user_effective_modules(session, guru)
    slip_types = _enabled_slip_types(effective)
    spt_types = enabled_spt_return_types(effective)

    own = session.exec(
        select(SchoolClass)
        .where(SchoolClass.guru_id == guru.id)
        .order_by(SchoolClass.archived, SchoolClass.name)
    ).all()
    own_ids = [c.id for c in own]
    if class_id is not None and class_id not in own_ids:
        return None
    if class_id is None and own:
        class_id = own[0].id

    counts = (
        dict(
            session.exec(
                select(Enrollment.class_id, func.count())
                .where(Enrollment.class_id.in_(own_ids))
                .group_by(Enrollment.class_id)
            ).all()
        )
        if own_ids
        else {}
    )
    pending = _pending_by_class(session, own_ids, effective)
    options = [
        GuruClassOption(
            id=c.id,
            name=c.name,
            academic_year=c.academic_year,
            archived=c.archived,
            student_count=counts.get(c.id, 0),
            pending_grading=pending.get(c.id, 0),
        )
        for c in own
    ]
    if class_id is None:
        return GuruDashboard(
            classes=options,
            class_id=None,
            students=[],
            grading_total=0,
            grading_by_module=[],
            grading_items=[],
        )

    students = session.exec(
        select(User)
        .join(Enrollment, Enrollment.siswa_id == User.id)
        .where(Enrollment.class_id == class_id, User.role == Role.siswa)
        .order_by(User.full_name)
    ).all()
    sids = [s.id for s in students]
    prog: dict[int, dict] = {sid: defaultdict(int) | {"last": None} for sid in sids}

    def touch(sid: int, key: str, n: int, last: datetime | None) -> None:
        p = prog.get(sid)
        if p is None:
            return
        p[key] += n
        p["last"] = _maxdt(p["last"], last)

    if sids:
        if slip_types:
            for sid, st, n, last in session.exec(
                select(
                    WithholdingSlip.siswa_id,
                    WithholdingSlip.status,
                    func.count(),
                    func.max(WithholdingSlip.updated_at),
                )
                .where(
                    WithholdingSlip.class_id == class_id,
                    WithholdingSlip.siswa_id.in_(sids),
                    WithholdingSlip.slip_type.in_(slip_types),
                )
                .group_by(WithholdingSlip.siswa_id, WithholdingSlip.status)
            ).all():
                if _val(st) == "issued":
                    touch(sid, "ebupot_issued", n, last)
                elif _val(st) == "draft":
                    touch(sid, "ebupot_draft", n, last)
                else:
                    touch(sid, "other", n, last)

        if "faktur_keluaran" in effective:
            for sid, st, n, last in session.exec(
                select(
                    TaxInvoice.siswa_id,
                    TaxInvoice.status,
                    func.count(),
                    func.max(TaxInvoice.updated_at),
                )
                .where(TaxInvoice.class_id == class_id, TaxInvoice.siswa_id.in_(sids))
                .group_by(TaxInvoice.siswa_id, TaxInvoice.status)
            ).all():
                touch(
                    sid,
                    "faktur_keluaran_issued" if _val(st) == "issued" else "other",
                    n,
                    last,
                )

        if "faktur_masukan" in effective:
            for sid, cs, n, last in session.exec(
                select(
                    TaxInputInvoice.siswa_id,
                    TaxInputInvoice.credit_status,
                    func.count(),
                    func.max(TaxInputInvoice.updated_at),
                )
                .where(
                    TaxInputInvoice.class_id == class_id,
                    TaxInputInvoice.siswa_id.in_(sids),
                )
                .group_by(TaxInputInvoice.siswa_id, TaxInputInvoice.credit_status)
            ).all():
                touch(
                    sid,
                    "faktur_masukan_credited" if _val(cs) == "credited" else "other",
                    n,
                    last,
                )

        if spt_types:
            for sid, rt, st, n, last in session.exec(
                select(
                    TaxReturn.siswa_id,
                    TaxReturn.return_type,
                    TaxReturn.status,
                    func.count(),
                    func.max(TaxReturn.updated_at),
                )
                .where(
                    TaxReturn.class_id == class_id,
                    TaxReturn.siswa_id.in_(sids),
                    TaxReturn.return_type.in_(spt_types),
                )
                .group_by(TaxReturn.siswa_id, TaxReturn.return_type, TaxReturn.status)
            ).all():
                if _val(st) == "reported":
                    key = {"ppn": "spt_ppn", "pph_unifikasi": "spt_pph_unifikasi"}.get(
                        _val(rt), "spt_pph_21_26"
                    )
                    touch(sid, "spt_reported", n, last)
                    touch(sid, key, 0, None)
                    prog[sid][key] += n
                else:
                    touch(sid, "other", n, last)

        for sid, n, last in session.exec(
            select(BillingCode.siswa_id, func.count(), func.max(BillingCode.updated_at))
            .where(
                BillingCode.siswa_id.in_(sids),
                BillingCode.tenant_id == guru.tenant_id,
                BillingCode.status == BillingStatus.paid,
            )
            .group_by(BillingCode.siswa_id)
        ).all():
            touch(sid, "billing_paid", n, last)

    # Grading queue: exact counts per module + the newest items, class-scoped.
    by_module: dict[str, int] = defaultdict(int)
    items: list[GradingItem] = []
    names = {s.id: s.full_name for s in students}
    pending_by_student: dict[int, int] = defaultdict(int)
    (_, slip_w), (_, kel_w), (_, mas_w), (_, spt_w) = _guru_grading_conditions(
        class_id, effective
    )
    for st_type, siswa_id, n in session.exec(
        select(WithholdingSlip.slip_type, WithholdingSlip.siswa_id, func.count())
        .where(slip_w)
        .group_by(WithholdingSlip.slip_type, WithholdingSlip.siswa_id)
    ).all():
        by_module[_val(st_type)] += n
        pending_by_student[siswa_id] += n
    for model, cond, mod in (
        (TaxInvoice, kel_w, MODULE_KELUARAN),
        (TaxInputInvoice, mas_w, MODULE_MASUKAN),
        (TaxReturn, spt_w, MODULE_SPT),
    ):
        for siswa_id, n in session.exec(
            select(model.siswa_id, func.count()).where(cond).group_by(model.siswa_id)
        ).all():
            by_module[mod] += n
            pending_by_student[siswa_id] += n

    def _push(rows, module_of, title_of):
        for r in rows:
            items.append(
                GradingItem(
                    module=module_of(r),
                    module_label=MODULE_LABELS.get(module_of(r), module_of(r)),
                    id=r.id,
                    title=title_of(r),
                    period=_period(r.tax_month, r.tax_year),
                    siswa_id=r.siswa_id,
                    siswa_name=names.get(r.siswa_id, "-"),
                    at=r.updated_at,
                )
            )

    _push(
        session.exec(
            select(WithholdingSlip)
            .where(slip_w)
            .order_by(WithholdingSlip.updated_at.desc())
            .limit(QUEUE_LIMIT)
        ).all(),
        lambda r: _val(r.slip_type),
        lambda r: r.recipient_name or r.withholding_number or f"Bukti potong #{r.id}",
    )
    _push(
        session.exec(
            select(TaxInvoice)
            .where(kel_w)
            .order_by(TaxInvoice.updated_at.desc())
            .limit(QUEUE_LIMIT)
        ).all(),
        lambda r: MODULE_KELUARAN,
        lambda r: r.buyer_name or r.invoice_number or f"Faktur #{r.id}",
    )
    _push(
        session.exec(
            select(TaxInputInvoice)
            .where(mas_w)
            .order_by(TaxInputInvoice.updated_at.desc())
            .limit(QUEUE_LIMIT)
        ).all(),
        lambda r: MODULE_MASUKAN,
        lambda r: r.seller_name or r.invoice_number or f"Faktur #{r.id}",
    )
    _push(
        session.exec(
            select(TaxReturn).where(spt_w).order_by(TaxReturn.updated_at.desc()).limit(QUEUE_LIMIT)
        ).all(),
        lambda r: MODULE_SPT,
        lambda r: SPT_TYPE_LABELS.get(_val(r.return_type), "SPT Masa"),
    )
    items.sort(key=lambda i: i.at or datetime.min.replace(tzinfo=None), reverse=True)

    rows = []
    for s in students:
        p = prog[s.id]
        total = sum(
            p[k]
            for k in (
                "ebupot_issued",
                "ebupot_draft",
                "faktur_keluaran_issued",
                "faktur_masukan_credited",
                "spt_reported",
                "billing_paid",
                "other",
            )
        )
        rows.append(
            GuruStudentProgress(
                id=s.id,
                full_name=s.full_name,
                identifier=s.username or s.email,
                ebupot_issued=p["ebupot_issued"],
                ebupot_draft=p["ebupot_draft"],
                faktur_keluaran_issued=p["faktur_keluaran_issued"],
                faktur_masukan_credited=p["faktur_masukan_credited"],
                spt_reported=p["spt_reported"],
                spt_ppn=p["spt_ppn"],
                spt_pph_unifikasi=p["spt_pph_unifikasi"],
                spt_pph_21_26=p["spt_pph_21_26"],
                billing_paid=p["billing_paid"],
                pending_grading=pending_by_student.get(s.id, 0),
                last_activity_at=p["last"],
                has_activity=total > 0,
            )
        )
    allowed_doc_types = enabled_assignment_doc_types(effective)
    if "tugas" in effective and allowed_doc_types:
        tugas_pending = session.exec(
            select(func.count())
            .select_from(AssignmentSubmission)
            .join(Assignment, Assignment.id == AssignmentSubmission.assignment_id)
            .where(
                Assignment.class_id == class_id,
                Assignment.doc_type.in_(allowed_doc_types),
                AssignmentSubmission.final_score.is_(None),
            )
        ).one()
    else:
        tugas_pending = 0
    return GuruDashboard(
        classes=options,
        class_id=class_id,
        students=rows,
        grading_total=sum(by_module.values()),
        grading_by_module=[
            GradingModuleCount(module=m, module_label=MODULE_LABELS.get(m, m), count=n)
            for m, n in sorted(by_module.items(), key=lambda kv: -kv[1])
        ],
        grading_items=items[:QUEUE_LIMIT],
        tugas_pending_grading=int(tugas_pending),
    )


# --- Siswa ----------------------------------------------------------------------


def _slip_doc(r: WithholdingSlip) -> SiswaDocument:
    mod = _val(r.slip_type)
    return SiswaDocument(
        module=mod,
        module_label=MODULE_LABELS.get(mod, mod),
        id=r.id,
        title=r.recipient_name or r.withholding_number or f"Bukti potong #{r.id}",
        period=_period(r.tax_month, r.tax_year),
        status=_val(r.status),
        amount=r.income_tax,
        score=r.score,
        teacher_feedback=r.teacher_feedback,
        at=r.updated_at,
    )


def _keluaran_doc(r: TaxInvoice) -> SiswaDocument:
    return SiswaDocument(
        module=MODULE_KELUARAN,
        module_label=MODULE_LABELS[MODULE_KELUARAN],
        id=r.id,
        title=r.buyer_name or r.invoice_number or f"Faktur #{r.id}",
        period=_period(r.tax_month, r.tax_year),
        status=_val(r.status),
        amount=r.total_ppn,
        score=r.score,
        teacher_feedback=r.teacher_feedback,
        at=r.updated_at,
    )


def _masukan_doc(r: TaxInputInvoice) -> SiswaDocument:
    return SiswaDocument(
        module=MODULE_MASUKAN,
        module_label=MODULE_LABELS[MODULE_MASUKAN],
        id=r.id,
        title=r.seller_name or r.invoice_number or f"Faktur #{r.id}",
        period=_period(r.tax_month, r.tax_year),
        status=_val(r.status),
        amount=r.total_ppn,
        score=r.score,
        teacher_feedback=r.teacher_feedback,
        at=r.updated_at,
    )


def _spt_doc(r: TaxReturn) -> SiswaDocument:
    return SiswaDocument(
        module=MODULE_SPT,
        module_label=MODULE_LABELS[MODULE_SPT],
        id=r.id,
        title=SPT_TYPE_LABELS.get(_val(r.return_type), "SPT Masa"),
        period=_period(r.tax_month, r.tax_year),
        status=_val(r.status),
        amount=r.amount_due,
        score=r.score,
        teacher_feedback=r.teacher_feedback,
        at=r.updated_at,
    )


def _newest(
    session: Session,
    siswa_id: int,
    limit: int,
    effective: set[str],
    *,
    scored: bool = False,
) -> list[SiswaDocument]:
    """Newest documents across enabled document tables (optionally only graded ones)."""
    from app.core.modules import enabled_spt_return_types

    docs: list[SiswaDocument] = []
    slip_types = _enabled_slip_types(effective)
    spt_types = enabled_spt_return_types(effective)

    sources = []
    if slip_types:
        sources.append(
            (WithholdingSlip, _slip_doc, [WithholdingSlip.slip_type.in_(slip_types)])
        )
    if "faktur_keluaran" in effective:
        sources.append((TaxInvoice, _keluaran_doc, []))
    if "faktur_masukan" in effective:
        sources.append((TaxInputInvoice, _masukan_doc, []))
    if spt_types:
        sources.append((TaxReturn, _spt_doc, [TaxReturn.return_type.in_(spt_types)]))

    for model, conv, extra_conds in sources:
        q = select(model).where(model.siswa_id == siswa_id, *extra_conds)
        if scored:
            q = q.where(model.score.is_not(None))
        q = q.order_by(model.updated_at.desc()).limit(limit)
        docs.extend(conv(r) for r in session.exec(q).all())
    docs.sort(key=lambda d: d.at or datetime.min.replace(tzinfo=None), reverse=True)
    return docs[:limit]


def build_siswa_dashboard(session: Session, siswa: User) -> SiswaDashboard:
    from app.core.deps import get_user_effective_modules
    from app.core.modules import enabled_assignment_doc_types, enabled_spt_return_types

    sid = siswa.id
    effective = get_user_effective_modules(session, siswa)
    slip_types = _enabled_slip_types(effective)
    spt_types = enabled_spt_return_types(effective)

    classes = session.exec(
        select(func.count()).select_from(Enrollment).where(Enrollment.siswa_id == sid)
    ).one()

    ebupot = StatusCounts()
    by_type: dict[str, StatusCounts] = {}
    if slip_types:
        for st_type, st, n in session.exec(
            select(WithholdingSlip.slip_type, WithholdingSlip.status, func.count())
            .where(
                WithholdingSlip.siswa_id == sid,
                WithholdingSlip.slip_type.in_(slip_types),
            )
            .group_by(WithholdingSlip.slip_type, WithholdingSlip.status)
        ).all():
            bucket = by_type.setdefault(_val(st_type), StatusCounts())
            setattr(bucket, _val(st), getattr(bucket, _val(st)) + n)
            setattr(ebupot, _val(st), getattr(ebupot, _val(st)) + n)
            bucket.total += n
            ebupot.total += n

    keluaran = StatusCounts()
    if "faktur_keluaran" in effective:
        for st, n in session.exec(
            select(TaxInvoice.status, func.count())
            .where(TaxInvoice.siswa_id == sid)
            .group_by(TaxInvoice.status)
        ).all():
            setattr(keluaran, _val(st), n)
            keluaran.total += n

    masukan = SiswaMasukanCounts()
    if "faktur_masukan" in effective:
        for st, cs, n in session.exec(
            select(TaxInputInvoice.status, TaxInputInvoice.credit_status, func.count())
            .where(TaxInputInvoice.siswa_id == sid)
            .group_by(TaxInputInvoice.status, TaxInputInvoice.credit_status)
        ).all():
            if _val(st) == "invalid":
                continue
            setattr(masukan, _val(cs), getattr(masukan, _val(cs)) + n)
            masukan.total += n

    spt = SiswaSptCounts()
    if spt_types:
        for st, n in session.exec(
            select(TaxReturn.status, func.count())
            .where(TaxReturn.siswa_id == sid, TaxReturn.return_type.in_(spt_types))
            .group_by(TaxReturn.status)
        ).all():
            setattr(spt, _val(st), n)
            spt.total += n
        spt.unreported = spt.draft + spt.awaiting_payment

    billing = SiswaBillingCounts()
    for st, n, amt in session.exec(
        select(BillingCode.status, func.count(), func.coalesce(func.sum(BillingCode.amount), 0))
        .where(BillingCode.siswa_id == sid)
        .group_by(BillingCode.status)
    ).all():
        if _val(st) == "unpaid":
            billing.unpaid = n
            billing.unpaid_amount = int(amt)
        elif _val(st) == "paid":
            billing.paid = n
    if "tagihan" in effective:
        billing.tagihan_open = session.exec(
            select(func.count())
            .select_from(Tagihan)
            .where(Tagihan.user_id == sid, Tagihan.status != TagihanStatus.lunas)
        ).one()
    else:
        billing.tagihan_open = 0

    grades = SiswaGradeStats()
    total_score = 0
    grade_sources = []
    if slip_types:
        grade_sources.append(
            (WithholdingSlip, [WithholdingSlip.slip_type.in_(slip_types)])
        )
    if "faktur_keluaran" in effective:
        grade_sources.append((TaxInvoice, []))
    if "faktur_masukan" in effective:
        grade_sources.append((TaxInputInvoice, []))
    if spt_types:
        grade_sources.append((TaxReturn, [TaxReturn.return_type.in_(spt_types)]))

    for model, extra_conds in grade_sources:
        n, s = session.exec(
            select(func.count(), func.coalesce(func.sum(model.score), 0)).where(
                model.siswa_id == sid, model.score.is_not(None), *extra_conds
            )
        ).one()
        grades.reviewed += n
        total_score += int(s)
    if grades.reviewed:
        grades.average = round(total_score / grades.reviewed, 1)

    # To-do list from real rows: drafts, unreported SPT, unpaid billing, uncredited masukan.
    todos: list[SiswaTodo] = []
    if slip_types:
        for r in session.exec(
            select(WithholdingSlip)
            .where(
                WithholdingSlip.siswa_id == sid,
                WithholdingSlip.status == SlipStatus.draft,
                WithholdingSlip.slip_type.in_(slip_types),
            )
            .order_by(WithholdingSlip.updated_at.desc())
            .limit(4)
        ).all():
            d = _slip_doc(r)
            todos.append(
                SiswaTodo(
                    kind="draft",
                    module=d.module,
                    id=d.id,
                    title=f"Terbitkan {d.module_label}: {d.title}",
                    detail=d.period,
                    module_label=d.module_label,
                    at=d.at,
                )
            )
    if "faktur_keluaran" in effective:
        for r in session.exec(
            select(TaxInvoice)
            .where(TaxInvoice.siswa_id == sid, TaxInvoice.status == TaxInvoiceStatus.draft)
            .order_by(TaxInvoice.updated_at.desc())
            .limit(3)
        ).all():
            d = _keluaran_doc(r)
            todos.append(
                SiswaTodo(
                    kind="draft",
                    module=d.module,
                    id=d.id,
                    title=f"Terbitkan faktur keluaran: {d.title}",
                    detail=d.period,
                    module_label=d.module_label,
                    at=d.at,
                )
            )
    if spt_types:
        for r in session.exec(
            select(TaxReturn)
            .where(
                TaxReturn.siswa_id == sid,
                TaxReturn.status != TaxReturnStatus.reported,
                TaxReturn.return_type.in_(spt_types),
            )
            .order_by(TaxReturn.updated_at.desc())
            .limit(4)
        ).all():
            d = _spt_doc(r)
            todos.append(
                SiswaTodo(
                    kind="spt",
                    module=d.module,
                    id=d.id,
                    title=f"Laporkan {d.title}",
                    detail=d.period,
                    module_label=d.module_label,
                    at=d.at,
                )
            )
    for r in session.exec(
        select(BillingCode)
        .where(BillingCode.siswa_id == sid, BillingCode.status == BillingStatus.unpaid)
        .order_by(BillingCode.updated_at.desc())
        .limit(3)
    ).all():
        todos.append(
            SiswaTodo(
                kind="billing",
                module="billing",
                id=r.id,
                title=f"Bayar kode billing {r.code}",
                detail=f"Rp {r.amount:,}".replace(",", "."),
                module_label="Pembayaran",
                at=r.updated_at,
            )
        )
    if "faktur_masukan" in effective:
        for r in session.exec(
            select(TaxInputInvoice)
            .where(
                TaxInputInvoice.siswa_id == sid,
                TaxInputInvoice.credit_status == TaxInputCreditStatus.not_credited,
                TaxInputInvoice.status != TaxInvoiceStatus.invalid,
            )
            .order_by(TaxInputInvoice.updated_at.desc())
            .limit(3)
        ).all():
            d = _masukan_doc(r)
            todos.append(
                SiswaTodo(
                    kind="masukan",
                    module=d.module,
                    id=d.id,
                    title=f"Kreditkan faktur masukan: {d.title}",
                    detail=d.period,
                    module_label=d.module_label,
                    at=d.at,
                )
            )

    # Open assignments (published in enrolled classes without a submission from this siswa)
    allowed_doc_types = enabled_assignment_doc_types(effective)
    if "tugas" in effective and allowed_doc_types:
        enrolled_cids = select(Enrollment.class_id).where(Enrollment.siswa_id == sid)
        submitted_aids = select(AssignmentSubmission.assignment_id).where(
            AssignmentSubmission.siswa_id == sid
        )
        open_assignments = list(
            session.exec(
                select(Assignment)
                .where(
                    Assignment.class_id.in_(enrolled_cids),
                    Assignment.status == AssignmentStatus.published,
                    Assignment.doc_type.in_(allowed_doc_types),
                    Assignment.id.not_in(submitted_aids),
                )
                .order_by(Assignment.due_at.asc().nulls_last(), Assignment.id.desc())
            ).all()
        )
    else:
        open_assignments = []
    open_tugas_count = len(open_assignments)
    for a in open_assignments[:4]:
        due_str = (
            f"Tenggat: {a.due_at.strftime('%d/%m/%Y %H:%M')}"
            if a.due_at
            else "Tanpa tenggat"
        )
        todos.append(
            SiswaTodo(
                kind="tugas",
                module="tugas",
                id=a.id,
                title=f"Kerjakan tugas: {a.title}",
                detail=due_str,
                module_label="Tugas Kelas",
                at=a.updated_at,
            )
        )

    todo_total = (
        ebupot.draft
        + keluaran.draft
        + spt.unreported
        + billing.unpaid
        + masukan.not_credited
        + open_tugas_count
    )

    return SiswaDashboard(
        classes=classes,
        ebupot=ebupot,
        ebupot_by_type=by_type,
        faktur_keluaran=keluaran,
        faktur_masukan=masukan,
        spt=spt,
        billing=billing,
        grades=grades,
        todos=todos,
        todo_total=todo_total,
        open_tugas_count=open_tugas_count,
        recent=_newest(session, sid, 6, effective),
        graded=_newest(session, sid, 4, effective, scored=True),
    )



# --- Tenants --------------------------------------------------------------------


def build_tenant_overviews(session: Session, tenants: list[Tenant]) -> list[TenantOverview]:
    ids = [t.id for t in tenants]
    if not ids:
        return []
    users: dict[tuple[int, str], int] = {}
    for tid, role, n in session.exec(
        select(User.tenant_id, User.role, func.count())
        .where(User.tenant_id.in_(ids))
        .group_by(User.tenant_id, User.role)
    ).all():
        users[(tid, _val(role))] = n
    kelas: dict[int, int] = {}
    active: dict[int, int] = {}
    for tid, archived, n in session.exec(
        select(SchoolClass.tenant_id, SchoolClass.archived, func.count())
        .where(SchoolClass.tenant_id.in_(ids))
        .group_by(SchoolClass.tenant_id, SchoolClass.archived)
    ).all():
        kelas[tid] = kelas.get(tid, 0) + n
        if not archived:
            active[tid] = active.get(tid, 0) + n

    def usage(used: int, limit: int | None) -> QuotaUsage:
        # 0 / NULL = unlimited, same convention as the quota enforcement.
        lim = limit or None
        return QuotaUsage(used=used, limit=lim, full=lim is not None and used >= lim)

    today = date.today()
    out = []
    for t in tenants:
        days = (t.berlaku_sampai - today).days if t.berlaku_sampai else None
        out.append(
            TenantOverview(
                tenant_id=t.id,
                name=t.name,
                slug=t.slug,
                type=_val(t.type),
                is_active=t.is_active,
                berlaku_dari=t.berlaku_dari,
                berlaku_sampai=t.berlaku_sampai,
                days_remaining=days,
                expired=days is not None and days < 0,
                expired_soon=days is not None and days <= 30,
                guru=usage(users.get((t.id, "guru"), 0), t.max_guru),
                siswa=usage(users.get((t.id, "siswa"), 0), t.max_siswa),
                kelas=usage(kelas.get(t.id, 0), t.max_kelas),
                active_classes=active.get(t.id, 0),
            )
        )
    return out


def build_platform_dashboard(session: Session) -> PlatformDashboard:
    tenants = list(session.exec(select(Tenant).order_by(Tenant.name)).all())
    overviews = build_tenant_overviews(session, tenants)
    return PlatformDashboard(
        tenants_total=len(overviews),
        tenants_active=sum(1 for o in overviews if o.is_active and not o.expired),
        tenants_expiring=sum(
            1 for o in overviews if o.is_active and not o.expired and o.expired_soon
        ),
        users=session.exec(select(func.count()).select_from(User)).one(),
        classes=session.exec(select(func.count()).select_from(SchoolClass)).one(),
        tenants=overviews,
    )
