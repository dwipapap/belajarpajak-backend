# Simulator Pajak — Backend

FastAPI + SQLModel backend for a multi-tenant **tax administration learning simulator** for Indonesian schools (SMK), universities, and training institutions. The backend now covers the platform foundation plus simulated e-Bupot BP21/BP23/BP26 workflows, e-Faktur Keluaran/Masukan, SPT Masa PPN + PPh Unifikasi, kode billing + pembayaran + PDF, audit trail, and editable tax tariff reference data.

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
- Simulated e-Bupot: BP21, BP23, BP26, BPPU, BPNR, SP, CY withholding slips backed by one `withholding_slips` table with `slip_type` as discriminator (`slips.py` factory `make_slip_router`). Numbers prefixed `SIM-`; BP21 supports SIM-TER (A/B/C Jan–Nov) vs progresif Desember with `calculation_method`; issue records signer + Kode Otorisasi simulasi and offers BPE-SIM PDF.
- Simulated e-Faktur: Faktur Keluaran (`/faktur-keluaran`) and Faktur Masukan (`/faktur-masukan`) with lines, issue/cancel, credit/uncredit, review/score.
- Simulated SPT Masa: PPN + PPh Unifikasi + PPh 21/26 (`/spt`, types `ppn` / `pph_unifikasi` / `pph21_26`) with posting, pernyataan, submit, pembetulan, billing linkage, review.
- Billing & payment: server-side `BillingCode` (`/billings`), pay flow, PDF generation (`billing_pdf.py`), KAP-KJS helper (`kjs.py`).
- Audit trail: `AuditLog` (`/audit-logs`) for submit/review/pay actions.
- Tax reference data: PTKP tiers and Pasal 17 progressive brackets used by BP21 non-final calculations.
- Classroom workflow: siswa create/issue slips/faktur/SPT, guru/admin review and score, tenant scoping enforced server-side.

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

The backend includes national tax portal-inspired e-Bupot simulator workflows for **BP21, BP23, BP26, BPPU, BPNR, SP, CY**. All seven modules share the same endpoint set and schema via `make_slip_router(SlipType, label)`; the router scopes records by `slip_type`.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/v1/{bp21\|bp23\|bp26\|bppu\|bpnr\|sp\|cy}` | List documents with status/class/student/month/year/facility filters |
| GET | `/api/v1/{bp21\|bp23\|bp26\|bppu\|bpnr\|sp\|cy}/summary` | Count draft, issued, invalid, and total documents |
| POST | `/api/v1/{bp21\|bp23\|bp26\|bppu\|bpnr\|sp\|cy}` | Create a draft and calculate PPh automatically |
| GET | `/api/v1/{bp21\|bp23\|bp26\|bppu\|bpnr\|sp\|cy}/{slip_id}` | Read one document |
| PATCH | `/api/v1/{bp21\|bp23\|bp26\|bppu\|bpnr\|sp\|cy}/{slip_id}` | Update a draft document |
| DELETE | `/api/v1/{bp21\|bp23\|bp26\|bppu\|bpnr\|sp\|cy}/{slip_id}` | Delete a draft document |
| POST | `/api/v1/{bp21\|bp23\|bp26\|bppu\|bpnr\|sp\|cy}/{slip_id}/issue` | Publish with signer + Kode Otorisasi simulasi, generate `SIM-` number |
| GET | `/api/v1/{bp21\|bp23\|bp26\|bppu\|bpnr\|sp\|cy}/{slip_id}/pdf` | Download BPE-SIM PDF + QR |
| POST | `/api/v1/{bp21\|bp23\|bp26\|bppu\|bpnr\|sp\|cy}/{slip_id}/cancel` | Cancel an issued document into invalid status |
| POST | `/api/v1/{bp21\|bp23\|bp26\|bppu\|bpnr\|sp\|cy}/{slip_id}/invalidate` | Mark a document invalid (superadmin/admin/guru) |
| PATCH | `/api/v1/{bp21\|bp23\|bp26\|bppu\|bpnr\|sp\|cy}/{slip_id}/spt-flag` | Set issued-document SPT/objection lifecycle flag |
| PATCH | `/api/v1/{bp21\|bp23\|bp26\|bppu\|bpnr\|sp\|cy}/{slip_id}/review` | Add score and teacher feedback |
| GET | `/api/v1/{bp21\|bp23\|bp26\|bppu\|bpnr\|sp\|cy}/import-template` | Download XML import template |
| POST | `/api/v1/{bp21\|bp23\|bp26\|bppu\|bpnr\|sp\|cy}/import-xml` | Import draft documents from XML (plus converter header aliases) |
| POST | `/api/v1/{bp21\|bp23\|bp26\|bppu\|bpnr\|sp\|cy}/bulk-issue` | Issue multiple drafts in one operation |
| GET | `/api/v1/{bp21\|bp23\|bp26\|bppu\|bpnr\|sp\|cy}/export-csv` | Export accessible documents as CSV |
| GET | `/api/v1/{bp21\|bp23\|bp26\|bppu\|bpnr\|sp\|cy}/export-xml` | Export accessible documents as XML |

Tax calculation behavior:

- BP21 non-final: SIM-TER (A/B/C, Jan–Nov) or PTKP + Pasal 17 progressive (Desember); `calculation_method` stored per slip.
- BP26/BPNR non-final can apply treaty rates when treaty country + SKD + tariff present.
- BP23/BPPU/SP/CY without NPWP (16 zeros) doubles the rate.
- `skb` and `rate_0` facilities force income tax to zero (SKB requires document number).

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

## Simulated e-Faktur modules

- **Faktur Keluaran** (`/api/v1/faktur-keluaran`): list, summary, export-csv, create (draft + server-side DPP/PPN/PPnBM incl. DPP Nilai Lain 11/12), bulk-issue, get/patch/delete draft, issue, cancel, review, plus line CRUD (`/{id}/lines`, `/{id}/lines/{line_id}`).
- **Faktur Masukan** (`/api/v1/faktur-masukan`): list, summary, export-csv, create, bulk-credit/uncredit/delete, get/patch/delete, credit/uncredit, cancel (admin-only), review, plus line CRUD. `credit_status` (`not_credited` / `credited`) drives SPT aggregation.

## Simulated SPT Masa module

SPT Masa PPN + PPh Unifikasi + PPh 21/26 (`/api/v1/spt`, `TaxReturnType`: `ppn`, `pph_unifikasi`, `pph21_26`; `TaxReturnKind`: `normal`, `pembetulan`; `TaxReturnStatus`: `draft`, `awaiting_payment`, `reported`):

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/v1/spt` | List returns |
| GET | `/api/v1/spt/summary` | Counts by status/type |
| POST | `/api/v1/spt` | Create konsep (draft) |
| GET | `/api/v1/spt/{return_id}` | Read one + lines |
| POST | `/api/v1/spt/{return_id}/posting` | Aggregate faktur/slip into lines (idempotent) |
| POST | `/api/v1/spt/{return_id}/pernyataan` | Sign statement (`wajib_pajak` / `kuasa_wajib_pajak`) |
| POST | `/api/v1/spt/{return_id}/submit` | Submit; creates billing server-side if kurang bayar |
| POST | `/api/v1/spt/{return_id}/pembetulan` | Correction (same-row new version) |
| GET | `/api/v1/spt/{return_id}/billing` | Linked billing code |
| PATCH | `/api/v1/spt/{return_id}/review` | Score + feedback (guru/admin) |
| DELETE | `/api/v1/spt/{return_id}` | Delete draft |

## Billing, payment, PDF & audit

- **Billings** (`/api/v1/billings`): list, detail, PDF (`/{billing_id}/pdf` via `billing_pdf.py`), pay (`/{billing_id}/pay` → `paid`, SPT becomes `reported`). `BillingCode` is a 15-digit `{KAP-KJS:6}{MM:2}{YY:2}{serial:5}`, `BillingStatus` `unpaid` / `paid`.
- **Billing mandiri** (`POST /api/v1/billings`): Layanan Mandiri Kode Billing — one code, one KAP-KJS (whitelist 5 in `services/kjs.py` + human labels), one `masa_pajak` MMYYYY (no future), one `amount` 1..9.999.999.999, optional `uraian` ≤200. `spt_id=NULL`, payable/printable via existing endpoints, expiry display 14 hari (PENG-4/PJ/2025, no cron). Siswa self; admin/guru specify same-tenant siswa; superadmin read-only.
- **Audit logs** (`/api/v1/audit-logs`, admin/superadmin): `GET` list of `AuditLog` entries written on SPT submit/review/pay.

## Scope & what's next

**Done:** multi-tenant auth (login/refresh/me/change-password), RBAC (`superadmin`, `admin`, `guru`, `siswa`), tenant CRUD, user CRUD (tenant-scoped), classes + enrollments, role-shaped dashboard summaries, simulated e-Bupot BP21/BP23/BP26/BPPU/BPNR/SP/CY (SIM-TER, signer + BPE-SIM PDF, Bukti Potong Saya), e-Faktur Keluaran/Masukan, SPT Masa PPN + PPh Unifikasi + PPh 21/26 (posting/pernyataan/submit/pembetulan/billing), kode billing + pay + PDF generation, audit trail, PTKP/progressive tariff management, seed data, Alembic migrations, smoke tests.

**Next phases:** retur / dokumen lain / prepopulated e-Faktur, SPT Tahunan OP/Badan, billing mandiri + Buku Besar, Profil WP/NPWP-NITKU, LMS (gradebook, bank soal, ujian, rapor, notifikasi). All future tenant-scoped tables should carry `tenant_id` and reuse the same server-side tenancy pattern.