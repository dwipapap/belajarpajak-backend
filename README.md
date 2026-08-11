# Simulator Pajak — Backend

FastAPI + SQLModel backend for a multi-tenant **tax administration learning simulator** for Indonesian schools (SMK), universities, and training institutions. The backend now covers the platform foundation plus simulated e-Bupot BP21/BP26 workflows and editable tax tariff reference data.

The frontend lives in a **separate repository**: `pajak-simulator-frontend` (Nuxt + Nuxt UI).

> **New here?** Read [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md) — full architecture,
> data model, RBAC rules, API reference, tax calculation engine, and conventions.

## Tech stack

- Python 3.12+ · FastAPI · SQLModel · Alembic · uv
- PostgreSQL ≥ 14, provided via:
  - **Windows**: existing local install (e.g. via Laragon — not managed by this repo), **or**
  - **Linux / WSL**: Docker Compose (`docker-compose.yml` included in this repo)
- JWT (access + refresh) with `pyjwt`, password hashing with `passlib[bcrypt]`

## Current module coverage

- Platform foundation: JWT auth, refresh tokens, RBAC, tenant isolation, users, classes, enrollments, role-shaped dashboard summaries.
- Simulated e-Bupot: BP21 and BP26 withholding slips backed by one `withholding_slips` table with `slip_type` as discriminator.
- Tax reference data: PTKP tiers and Pasal 17 progressive brackets used by BP21 non-final calculations.
- Classroom workflow: siswa create/issue slips, guru/admin review and score, tenant scoping enforced server-side.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (`pip install uv` or the standalone installer; ensure `uv` is on PATH)
- A running local PostgreSQL ≥ 14:
  - **Linux / WSL**: Docker & Docker Compose (the included `docker-compose.yml` provisions PostgreSQL 16)
  - **Windows**: an existing local install (e.g. via Laragon) — not managed by this repo

## One-time database bootstrap

### Option A — Linux / WSL (Docker Compose)

From the repo root, start the PostgreSQL container:

```bash
docker compose up -d
```

This provisions a `pajaksim` database owned by the `pajaksim` role, reachable at `localhost:5432`. The container is configured with a health check and `restart: unless-stopped`.

### Option B — Windows (Laragon / manual install)

Run `psql` from your Postgres `bin` directory (Laragon: `C:\laragon\bin\postgresql\postgresql\bin`) as the `postgres` user:

```sql
CREATE ROLE pajaksim WITH LOGIN PASSWORD 'pajaksim_dev';
CREATE DATABASE pajaksim OWNER pajaksim;
```

## Configuration

```bash
cp .env.example .env
```

The connection string lives ONLY in `.env`:

```
DATABASE_URL=postgresql+psycopg://pajaksim:pajaksim_dev@localhost:5432/pajaksim
APP_ENV=development
```

If your Postgres runs on a non-default port, edit `.env` — nothing else changes. Every variable is documented in `.env.example`.

Production must set `APP_ENV=production`, replace `SECRET_KEY` with a random secret, and set `AUTO_SEED=false`. The app refuses to start with unsafe production defaults.

## Run (from the repo root)

```bash
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

```bash
uv run ruff check .
```

Tests require a dedicated Postgres database whose name clearly indicates a test database (e.g. `pajaksim_test`); otherwise the suite **skips** instead of mutating a development database. Create the test database once, apply migrations to it, then run the suite with `DATABASE_URL` pointed at it:

```bash
# Create the test database (one-time)
docker compose exec -T db psql -U pajaksim -c "CREATE DATABASE pajaksim_test OWNER pajaksim;"

# Apply migrations to the test database (one-time / after new migrations)
DATABASE_URL="postgresql+psycopg://pajaksim:pajaksim_dev@localhost:5432/pajaksim_test" \
  uv run alembic upgrade head

# Run the test suite against the test database
DATABASE_URL="postgresql+psycopg://pajaksim:pajaksim_dev@localhost:5432/pajaksim_test" \
  uv run pytest
```

If the test database is unreachable the suite skips with an explanatory message.

## Implementation notes / resolutions

- **bcrypt pinned `<4.1`**: passlib 1.7.4 reads `bcrypt.__about__.__version__`, which bcrypt 4.1+ removed. Hashing works either way; the pin silences a noisy startup warning.
- **Email validation**: request schemas use a lightweight pattern instead of `EmailStr`, because the RFC validator rejects reserved TLDs like `.local` used by the (deliberately non-routable) seed accounts.
- **Token storage tradeoff**: the frontend keeps the access token in memory and the refresh token in `localStorage`. Acceptable for current development; revisit (httpOnly cookies) before production.
- **Dev environment**: Linux/WSL dev runs PostgreSQL via the included `docker-compose.yml`. Windows dev may use Laragon's existing PostgreSQL install or Docker Desktop with the same compose file.

## Simulated e-Bupot modules

The backend includes national tax portal-inspired e-Bupot simulator workflows for **BP21** and **BP26**. Both modules share the same endpoint set and schema; the router scopes records by `slip_type`.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/v1/{bp21\|bp26}` | List documents with status/class/student/month/year/facility filters |
| GET | `/api/v1/{bp21\|bp26}/summary` | Count draft, issued, invalid, and total documents |
| POST | `/api/v1/{bp21\|bp26}` | Create a draft and calculate PPh automatically |
| GET | `/api/v1/{bp21\|bp26}/{slip_id}` | Read one document |
| PATCH | `/api/v1/{bp21\|bp26}/{slip_id}` | Update a draft document |
| DELETE | `/api/v1/{bp21\|bp26}/{slip_id}` | Delete a draft document |
| POST | `/api/v1/{bp21\|bp26}/{slip_id}/issue` | Publish a draft and generate a withholding number |
| POST | `/api/v1/{bp21\|bp26}/{slip_id}/cancel` | Cancel an issued document into invalid status |
| POST | `/api/v1/{bp21\|bp26}/{slip_id}/invalidate` | Mark a document invalid (superadmin/admin/guru) |
| PATCH | `/api/v1/{bp21\|bp26}/{slip_id}/spt-flag` | Set issued-document SPT/objection lifecycle flag |
| PATCH | `/api/v1/{bp21\|bp26}/{slip_id}/review` | Add score and teacher feedback |
| GET | `/api/v1/{bp21\|bp26}/import-template` | Download XML import template |
| POST | `/api/v1/{bp21\|bp26}/import-xml` | Import draft documents from XML |
| POST | `/api/v1/{bp21\|bp26}/bulk-issue` | Issue multiple drafts in one operation |
| GET | `/api/v1/{bp21\|bp26}/export-csv` | Export accessible documents as CSV |
| GET | `/api/v1/{bp21\|bp26}/export-xml` | Export accessible documents as XML |

Tax calculation behavior:

- BP21 non-final uses PTKP and Pasal 17 progressive annual brackets when matching active tariff data exists.
- BP26 non-final can apply treaty rates when treaty country and tariff fields are present.
- `skb` and `rate_0` facilities force income tax to zero.

Tax tariff endpoints:

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/v1/tarif-pajak/ptkp` | List PTKP tiers |
| POST | `/api/v1/tarif-pajak/ptkp` | Create PTKP tier (superadmin) |
| PATCH | `/api/v1/tarif-pajak/ptkp/{ptkp_id}` | Update PTKP tier (superadmin) |
| DELETE | `/api/v1/tarif-pajak/ptkp/{ptkp_id}` | Delete PTKP tier (superadmin) |
| GET | `/api/v1/tarif-pajak/progresif` | List Pasal 17 progressive brackets |
| POST | `/api/v1/tarif-pajak/progresif` | Create progressive bracket (superadmin) |
| PATCH | `/api/v1/tarif-pajak/progresif/{bracket_id}` | Update progressive bracket (superadmin) |
| DELETE | `/api/v1/tarif-pajak/progresif/{bracket_id}` | Delete progressive bracket (superadmin) |

Access follows the existing tenancy/RBAC pattern:

- `siswa`: create, edit, issue, and view their own documents.
- `guru`: view/review documents in classes they teach.
- `admin`: manage documents inside their own tenant.
- `superadmin`: global read/review access.

## Scope & what's next

**Done:** multi-tenant auth (login/refresh/me), RBAC (`superadmin`, `admin`, `guru`, `siswa`), tenant CRUD, user CRUD (tenant-scoped), classes + enrollments, role-shaped dashboard summaries, simulated e-Bupot BP21/BP26 workflows, PTKP/progressive tariff management, seed data, Alembic migrations, smoke tests.

**Next phases:** faktur pajak simulasi, bupot PPh 23, kode billing, SPT simulasi, PDF generation, expanded grading, reports. All future tenant-scoped tables should carry `tenant_id` and reuse the same server-side tenancy pattern.