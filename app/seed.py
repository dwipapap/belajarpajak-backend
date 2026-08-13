"""Idempotent seed script.

Run standalone:  uv run python -m app.seed
Auto-invoked on backend startup via seed_if_empty() when AUTO_SEED=true and the DB is empty.
Safe to re-run: every insert is guarded by an existence check.
"""

from __future__ import annotations

from datetime import date

from sqlmodel import Session, func, select

from app.core.security import hash_password
from app.db import engine
from app.models.enrollment import Enrollment
from app.models.enums import Role, TenantType
from app.models.school_class import SchoolClass
from app.models.tarif_pajak import TarifProgresifPasal17, TierPtkp
from app.models.tax_invoice import TaxInvoice
from app.models.tenant import Tenant
from app.models.user import User
from app.routers.tax_invoices import build_line, issue_invoice, recalculate_invoice
from app.schemas.tax_invoice import TaxInvoiceLineCreate

SEED_PASSWORD = "Password123!"
TARIF_YEARS = (2024, 2025, 2026)

PTKP_DEFAULTS: dict[str, int] = {
    "TK/0": 54_000_000,
    "TK/1": 58_500_000,
    "TK/2": 63_000_000,
    "TK/3": 67_500_000,
    "K/0": 58_500_000,
    "K/1": 63_000_000,
    "K/2": 67_500_000,
    "K/3": 72_000_000,
}

PROGRESSIVE_DEFAULTS: tuple[tuple[int, int | None, int], ...] = (
    (0, 60_000_000, 500),
    (60_000_000, 250_000_000, 1500),
    (250_000_000, 500_000_000, 2500),
    (500_000_000, 5_000_000_000, 3000),
    (5_000_000_000, None, 3500),
)

TENANTS = [
    {"name": "SMK Negeri 1 Pekanbaru", "slug": "smkn1-pku", "type": TenantType.smk},
    {"name": "Politeknik Caltex Riau", "slug": "pcr", "type": TenantType.kampus},
]

SUPERADMIN_EMAIL = "super@pajaksim.local"


def _get_or_create_user(
    session: Session,
    *,
    email: str,
    full_name: str,
    role: Role,
    tenant_id: int | None,
    password_hash: str,
) -> User:
    existing = session.exec(
        select(User).where(User.email == email, User.tenant_id == tenant_id)
    ).first()
    if existing is not None:
        return existing
    user = User(
        email=email,
        full_name=full_name,
        role=role,
        tenant_id=tenant_id,
        password_hash=password_hash,
    )
    session.add(user)
    session.flush()  # assign PK without committing
    return user


def seed(session: Session) -> list[dict]:
    """Create tenants, users, classes, enrollments. Returns credential rows for printing."""
    pw_hash = hash_password(SEED_PASSWORD)
    credentials: list[dict] = []

    # Superadmin (global, tenant_id NULL)
    _get_or_create_user(
        session,
        email=SUPERADMIN_EMAIL,
        full_name="Super Administrator",
        role=Role.superadmin,
        tenant_id=None,
        password_hash=pw_hash,
    )
    credentials.append({"tenant": "-", "role": "superadmin", "email": SUPERADMIN_EMAIL})

    for spec in TENANTS:
        tenant = session.exec(select(Tenant).where(Tenant.slug == spec["slug"])).first()
        if tenant is None:
            tenant = Tenant(name=spec["name"], slug=spec["slug"], type=spec["type"])
            session.add(tenant)
            session.flush()

        slug = spec["slug"]

        admin = _get_or_create_user(
            session,
            email=f"admin@{slug}.local",
            full_name=f"Admin {tenant.name}",
            role=Role.admin,
            tenant_id=tenant.id,
            password_hash=pw_hash,
        )
        credentials.append({"tenant": slug, "role": "admin", "email": admin.email})

        gurus: list[User] = []
        for i in (1, 2):
            guru = _get_or_create_user(
                session,
                email=f"guru{i}@{slug}.local",
                full_name=f"Guru {i} {tenant.name}",
                role=Role.guru,
                tenant_id=tenant.id,
                password_hash=pw_hash,
            )
            gurus.append(guru)
            credentials.append({"tenant": slug, "role": "guru", "email": guru.email})

        siswa_list: list[User] = []
        for i in range(1, 7):
            siswa = _get_or_create_user(
                session,
                email=f"siswa{i}@{slug}.local",
                full_name=f"Siswa {i} {tenant.name}",
                role=Role.siswa,
                tenant_id=tenant.id,
                password_hash=pw_hash,
            )
            siswa_list.append(siswa)
            credentials.append({"tenant": slug, "role": "siswa", "email": siswa.email})

        # Two classes: guru1 owns "XII Akuntansi 1" (siswa 1-3),
        #              guru2 owns "XII Akuntansi 2" (siswa 4-6).
        class_specs = [
            ("XII Akuntansi 1", gurus[0], siswa_list[0:3]),
            ("XII Akuntansi 2", gurus[1], siswa_list[3:6]),
        ]
        first_class: SchoolClass | None = None
        for name, guru, members in class_specs:
            school_class = session.exec(
                select(SchoolClass).where(
                    SchoolClass.tenant_id == tenant.id, SchoolClass.name == name
                )
            ).first()
            if school_class is None:
                school_class = SchoolClass(
                    tenant_id=tenant.id,
                    name=name,
                    academic_year="2026/2027",
                    guru_id=guru.id,
                )
                session.add(school_class)
                session.flush()
            if first_class is None:
                first_class = school_class
            for siswa in members:
                exists = session.exec(
                    select(Enrollment).where(
                        Enrollment.class_id == school_class.id,
                        Enrollment.siswa_id == siswa.id,
                    )
                ).first()
                if exists is None:
                    session.add(Enrollment(class_id=school_class.id, siswa_id=siswa.id))

        seed_faktur_examples(
            session, tenant=tenant, siswa=siswa_list[0], school_class=first_class
        )

    seed_tarif_pajak(session)
    session.commit()
    return credentials


def _print_credentials(credentials: list[dict]) -> None:
    print("\n" + "=" * 60)
    print("SEED SELESAI — kredensial (kata sandi untuk SEMUA akun: "
          f"{SEED_PASSWORD})")
    print("=" * 60)
    print(f"{'TENANT':<12} {'ROLE':<12} {'EMAIL'}")
    print("-" * 60)
    for row in credentials:
        print(f"{row['tenant']:<12} {row['role']:<12} {row['email']}")
    print("=" * 60 + "\n")


#: Two sample faktur keluaran per tenant so the Pajak Keluaran list is not empty
#: on first login: one draft to open and edit, one already issued.
FAKTUR_EXAMPLES: tuple[dict, ...] = (
    {
        "reference": "Contoh faktur draft",
        "transaction_code": "01",
        "buyer_identity_number": "0013457668062000",
        "buyer_name": "PT MITRA NIAGA SIMULASI",
        "buyer_address": "JL POS PENGUMBEN RAYA NO.8, JAKARTA BARAT",
        "issue": False,
        "lines": [
            {
                "line_type": "jasa",
                "item_code": "060000",
                "item_name": "Biaya Kirim Barang",
                "unit": "Bulan",
                "unit_price": 2_500_000,
                "quantity": 1,
            }
        ],
    },
    {
        "reference": "Contoh faktur telah terbit",
        "transaction_code": "05",
        "buyer_identity_number": "0987654321098000",
        "buyer_name": "CV KARYA BERSAMA SIMULASI",
        "buyer_address": "JL ARIFIN AHMAD NO.102, PEKANBARU",
        "issue": True,
        "lines": [
            {
                "line_type": "barang",
                "item_code": "010000",
                "item_name": "Alat Tulis Kantor",
                "unit": "Paket",
                "unit_price": 750_000,
                "quantity": 4,
            },
            {
                "line_type": "jasa",
                "item_code": "060000",
                "item_name": "Jasa Bongkar Muat",
                "unit": "Unit",
                "unit_price": 300_000,
                "quantity": 2,
            },
        ],
    },
)


def seed_faktur_examples(
    session: Session,
    *,
    tenant: Tenant,
    siswa: User,
    school_class: SchoolClass | None,
) -> None:
    """Create the sample Pajak Keluaran rows, keyed by reference so re-runs are safe."""
    for spec in FAKTUR_EXAMPLES:
        exists = session.exec(
            select(TaxInvoice).where(
                TaxInvoice.tenant_id == tenant.id,
                TaxInvoice.siswa_id == siswa.id,
                TaxInvoice.reference == spec["reference"],
            )
        ).first()
        if exists is not None:
            continue

        invoice = TaxInvoice(
            tenant_id=tenant.id,
            class_id=school_class.id if school_class else None,
            siswa_id=siswa.id,
            created_by_id=siswa.id,
            transaction_code=spec["transaction_code"],
            invoice_date=date(2026, 7, 18),
            tax_month=7,
            tax_year=2026,
            reference=spec["reference"],
            seller_npwp="1471110802000001",
            seller_name=tenant.name,
            seller_address="JALAN ARIFIN AHMAD NO.102, PEKANBARU",
            seller_idtku="000000",
            buyer_identity_number=spec["buyer_identity_number"],
            buyer_name=spec["buyer_name"],
            buyer_address=spec["buyer_address"],
            buyer_idtku="000000",
        )
        session.add(invoice)
        session.flush()

        for order, line_spec in enumerate(spec["lines"]):
            session.add(
                build_line(
                    TaxInvoiceLineCreate.model_validate(line_spec),
                    invoice_id=invoice.id,
                    line_order=order,
                )
            )
        session.flush()
        recalculate_invoice(session, invoice)

        if spec["issue"]:
            issue_invoice(session, invoice)
            session.add(invoice)
        session.flush()


def seed_faktur_for_all_tenants(session: Session) -> None:
    """Ensure the sample faktur exist on an already-seeded database."""
    for tenant in session.exec(select(Tenant)).all():
        siswa = session.exec(
            select(User)
            .where(User.tenant_id == tenant.id, User.role == Role.siswa)
            .order_by(User.id)
        ).first()
        if siswa is None:
            continue
        school_class = session.exec(
            select(SchoolClass).where(SchoolClass.tenant_id == tenant.id).order_by(SchoolClass.id)
        ).first()
        seed_faktur_examples(session, tenant=tenant, siswa=siswa, school_class=school_class)


def seed_tarif_pajak(session: Session) -> None:
    for tahun in TARIF_YEARS:
        for status_kode, jumlah_ptkp in PTKP_DEFAULTS.items():
            exists = session.exec(
                select(TierPtkp).where(
                    TierPtkp.status_kode == status_kode,
                    TierPtkp.tahun_pajak == tahun,
                )
            ).first()
            if exists is None:
                session.add(
                    TierPtkp(
                        status_kode=status_kode,
                        jumlah_ptkp=jumlah_ptkp,
                        tahun_pajak=tahun,
                    )
                )

        for batas_bawah, batas_atas, persentase_basis_points in PROGRESSIVE_DEFAULTS:
            exists = session.exec(
                select(TarifProgresifPasal17).where(
                    TarifProgresifPasal17.batas_bawah == batas_bawah,
                    TarifProgresifPasal17.tahun_pajak == tahun,
                )
            ).first()
            if exists is None:
                session.add(
                    TarifProgresifPasal17(
                        batas_bawah=batas_bawah,
                        batas_atas=batas_atas,
                        persentase_basis_points=persentase_basis_points,
                        tahun_pajak=tahun,
                    )
                )


def seed_if_empty() -> None:
    """Seed users only when empty; always ensure tariff defaults exist."""
    with Session(engine) as session:
        user_count = session.exec(select(func.count()).select_from(User)).one()
        if user_count > 0:
            seed_tarif_pajak(session)
            seed_faktur_for_all_tenants(session)
            session.commit()
            return
        credentials = seed(session)
        _print_credentials(credentials)


def main() -> None:
    with Session(engine) as session:
        credentials = seed(session)
    _print_credentials(credentials)


if __name__ == "__main__":
    main()
