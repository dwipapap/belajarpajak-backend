"""Idempotent seed script.

Run standalone:  uv run python -m app.seed
Auto-invoked on backend startup via seed_if_empty() when AUTO_SEED=true and the DB is empty.
Safe to re-run: every insert is guarded by an existence check.
"""

from __future__ import annotations

from sqlmodel import Session, func, select

from app.core.security import hash_password
from app.db import engine
from app.models.enrollment import Enrollment
from app.models.enums import Role, TenantType
from app.models.school_class import SchoolClass
from app.models.tenant import Tenant
from app.models.user import User

SEED_PASSWORD = "Password123!"

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
            for siswa in members:
                exists = session.exec(
                    select(Enrollment).where(
                        Enrollment.class_id == school_class.id,
                        Enrollment.siswa_id == siswa.id,
                    )
                ).first()
                if exists is None:
                    session.add(Enrollment(class_id=school_class.id, siswa_id=siswa.id))

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


def seed_if_empty() -> None:
    """Seed only when the users table is empty (used for auto-seed on startup)."""
    with Session(engine) as session:
        user_count = session.exec(select(func.count()).select_from(User)).one()
        if user_count > 0:
            return
        credentials = seed(session)
        _print_credentials(credentials)


def main() -> None:
    with Session(engine) as session:
        credentials = seed(session)
    _print_credentials(credentials)


if __name__ == "__main__":
    main()
