# Simulator Pajak — Backend (Phase 1: Foundation)

FastAPI + SQLModel backend for a multi-tenant **tax administration learning simulator** for Indonesian schools (SMK), universities, and training institutions. Phase 1 delivers the foundation only: JWT auth, roles (RBAC), tenancy, and dashboard summary endpoints.

The frontend lives in a **separate repository**: `pajak-simulator-frontend` (Nuxt + Nuxt UI).

## Tech stack

- Python 3.12+ · FastAPI · SQLModel · Alembic · uv
- PostgreSQL ≥ 14 (existing local install via Laragon — not managed by this repo)
- JWT (access + refresh) with `pyjwt`, password hashing with `passlib[bcrypt]`

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (`pip install uv` or the standalone installer; ensure `uv` is on PATH)
- A running local PostgreSQL (e.g. via Laragon)

## One-time database bootstrap

Run `psql` from your Postgres `bin` directory (Laragon: `C:\laragon\bin\postgresql\postgresql\bin`) as the `postgres` user:

```sql
CREATE ROLE pajaksim WITH LOGIN PASSWORD 'pajaksim_dev';
CREATE DATABASE pajaksim OWNER pajaksim;
```

## Configuration

```powershell
Copy-Item .env.example .env
```

The connection string lives ONLY in `.env`:

```
DATABASE_URL=postgresql+psycopg://pajaksim:pajaksim_dev@localhost:5432/pajaksim
```

If your Postgres runs on a non-default port, edit `.env` — nothing else changes. Every variable is documented in `.env.example`.

## Run (PowerShell, from the repo root)

```powershell
uv sync
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:app --reload
```

- API: http://localhost:8000 — interactive docs at http://localhost:8000/docs
- On startup the app **auto-seeds** if the database is empty (`AUTO_SEED=true`), so the manual seed step is a convenience, not a requirement.
- The seed script is idempotent — safe to re-run any time.

## Seed credentials

Password for **all** seed accounts: `Password123!`

| Tenant | Role | Email |
|---|---|---|
| — | superadmin | `super@pajaksim.local` |
| smkn1-pku | admin | `admin@smkn1-pku.local` |
| smkn1-pku | guru | `guru1@smkn1-pku.local`, `guru2@smkn1-pku.local` |
| smkn1-pku | siswa | `siswa1@smkn1-pku.local` … `siswa6@smkn1-pku.local` |
| pcr | admin | `admin@pcr.local` |
| pcr | guru | `guru1@pcr.local`, `guru2@pcr.local` |
| pcr | siswa | `siswa1@pcr.local` … `siswa6@pcr.local` |

Tenants: **SMK Negeri 1 Pekanbaru** (`smkn1-pku`) and **Politeknik Caltex Riau** (`pcr`). Each has 2 classes ("XII Akuntansi 1/2") with 3 enrolled siswa each.

## Port map

| Service | Port |
|---|---|
| Frontend (separate repo) | 3000 |
| Backend (this repo) | 8000 |
| PostgreSQL | 5432 |

## Tests & lint

```powershell
uv run pytest
uv run ruff check .
```

Tests run against the Postgres in `DATABASE_URL` (no sqlite fallback, per spec). If the database is unreachable the suite **skips** with an explanatory message instead of failing.

## Implementation notes / resolutions

- **bcrypt pinned `<4.1`**: passlib 1.7.4 reads `bcrypt.__about__.__version__`, which bcrypt 4.1+ removed. Hashing works either way; the pin silences a noisy startup warning.
- **Email validation**: request schemas use a lightweight pattern instead of `EmailStr`, because the RFC validator rejects reserved TLDs like `.local` used by the (deliberately non-routable) seed accounts.
- **Token storage tradeoff**: the frontend keeps the access token in memory and the refresh token in `localStorage`. Acceptable for Phase 1 dev; revisit (httpOnly cookies) before production.
- **Docker deliberately deferred** to the deployment phase — dev is Windows-native against Laragon's Postgres.

## Phase 1 scope & what's next

**In scope (done):** multi-tenant auth (login/refresh/me), RBAC (`superadmin`, `admin`, `guru`, `siswa`), tenant CRUD, user CRUD (tenant-scoped), classes + enrollments, role-shaped dashboard summaries, seed data, Alembic migrations, smoke tests.

**Next phases (not in this repo yet):** faktur pajak simulasi, bupot PPh 21/23, kode billing, SPT simulasi, PDF generation, grading, reports. All future tables will carry `tenant_id` and reuse the same tenancy pattern (`tenant_filter` in `app/core/deps.py`).
