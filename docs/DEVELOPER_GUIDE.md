# Developer Guide — Simulator Pajak Backend

Dokumentasi lengkap arsitektur, modul, model data, endpoint, aturan bisnis, dan alur kerja
backend **Simulator Pajak** (FastAPI + SQLModel + PostgreSQL).

Dokumen ini melengkapi `README.md`. README fokus pada *cara menjalankan*; dokumen ini fokus
pada *apa isinya dan mengapa dibuat begitu*.

---

## Daftar Isi

1. [Ringkasan Produk](#1-ringkasan-produk)
2. [Tech Stack & Dependensi](#2-tech-stack--dependensi)
3. [Struktur Direktori](#3-struktur-direktori)
4. [Konfigurasi & Environment](#4-konfigurasi--environment)
5. [Bootstrap Aplikasi (`app/main.py`)](#5-bootstrap-aplikasi-appmainpy)
6. [Lapisan Inti (`app/core/`)](#6-lapisan-inti-appcore)
7. [Model Data & Skema Database](#7-model-data--skema-database)
8. [Enumerasi](#8-enumerasi)
9. [Model RBAC & Multi-Tenancy](#9-model-rbac--multi-tenancy)
10. [Referensi API Lengkap](#10-referensi-api-lengkap)
11. [Modul e-Bupot (BP21 / BP26)](#11-modul-e-bupot-bp21--bp26)
12. [Mesin Perhitungan Pajak](#12-mesin-perhitungan-pajak)
13. [Import / Export](#13-import--export)
14. [Migrasi Database (Alembic)](#14-migrasi-database-alembic)
15. [Seed Data](#15-seed-data)
16. [Testing](#16-testing)
17. [Konvensi Kode](#17-konvensi-kode)
18. [Cara Menambah Fitur Baru](#18-cara-menambah-fitur-baru)
19. [Catatan Teknis & Utang Teknis](#19-catatan-teknis--utang-teknis)

---

## 1. Ringkasan Produk

Backend untuk **simulator pembelajaran administrasi perpajakan** multi-tenant, ditujukan
untuk SMK, kampus, dan lembaga pelatihan di Indonesia. Siswa mengisi dokumen perpajakan
tiruan (meniru portal e-Bupot), guru menilai, admin mengelola institusinya.

**Aktor:**

| Peran | Cakupan | Kemampuan utama |
|---|---|---|
| `superadmin` | Global (tanpa tenant) | Kelola tenant, semua user, data tarif pajak, baca semua dokumen |
| `admin` | Satu tenant | Kelola guru/siswa & kelas di tenantnya, kelola dokumen tenant |
| `guru` | Kelas yang diajar | Lihat & nilai dokumen siswa di kelasnya |
| `siswa` | Dirinya sendiri | Buat, ubah, terbitkan, dan lihat dokumennya sendiri |

**Frontend** berada di repositori terpisah: `pajak-simulator-frontend` (Nuxt + Nuxt UI),
berjalan di port 3000.

---

## 2. Tech Stack & Dependensi

| Komponen | Pilihan | Catatan |
|---|---|---|
| Bahasa | Python ≥ 3.12 | Memakai fitur 3.12 (`type` params generik, `StrEnum`, `datetime.UTC`) |
| Web framework | FastAPI ≥ 0.115 | OpenAPI otomatis di `/docs` |
| ORM | SQLModel ≥ 0.0.22 | SQLAlchemy + Pydantic |
| Driver DB | `psycopg[binary]` v3 | URL: `postgresql+psycopg://…` |
| Migrasi | Alembic ≥ 1.14 | URL disuntik runtime dari settings |
| Konfigurasi | pydantic-settings ≥ 2.6 | Membaca `backend/.env` |
| JWT | `pyjwt` ≥ 2.10 | HS256 |
| Hash password | `passlib[bcrypt]`, `bcrypt >=4.0,<4.1` | Pin bcrypt disengaja (lihat §19) |
| Server | uvicorn (standard) | |
| Upload | `python-multipart` | Untuk import XML |
| Package manager | `uv` | `[tool.uv] package = false` — bukan paket terinstal |
| Lint | ruff (E, F, I, UP, B), line-length 100 | |
| Test | pytest + httpx (TestClient) | |

Database: **PostgreSQL ≥ 14** (Docker Compose menyediakan Postgres 16-alpine).

---

## 3. Struktur Direktori

```
backend/
├── alembic/
│   ├── env.py                    # URL & metadata diambil dari app, bukan alembic.ini
│   ├── script.py.mako
│   └── versions/                 # 8 revisi, rantai linear
├── app/
│   ├── main.py                   # create_app(): CORS, router, lifespan auto-seed
│   ├── db.py                     # engine + get_session() dependency
│   ├── seed.py                   # seed idempoten (tenant, user, kelas, tarif)
│   ├── core/
│   │   ├── config.py             # Settings (pydantic-settings) + validate_runtime_safety()
│   │   ├── security.py           # hash/verify password, create/decode JWT
│   │   └── deps.py               # SessionDep, CurrentUser, require_roles, tenant_filter
│   ├── models/                   # tabel SQLModel
│   │   ├── base.py               # TimestampMixin (created_at/updated_at)
│   │   ├── enums.py              # semua StrEnum bersama
│   │   ├── tenant.py, user.py, school_class.py, enrollment.py
│   │   ├── slip.py               # WithholdingSlip (satu tabel untuk BP21 & BP26)
│   │   └── tarif_pajak.py        # TierPtkp, TarifProgresifPasal17
│   ├── schemas/                  # Pydantic request/response (terpisah dari tabel)
│   │   ├── auth.py, user.py, tenant.py, school_class.py
│   │   ├── slip.py, tarif_pajak.py, dashboard.py
│   └── routers/
│       ├── auth.py, tenants.py, users.py, classes.py
│       ├── slips.py              # factory make_slip_router() → bp21_router, bp26_router
│       ├── tarif_pajak.py, dashboard.py
├── tests/                        # conftest + 4 file uji
├── docker-compose.yml            # PostgreSQL 16 lokal
├── alembic.ini
├── pyproject.toml
└── .env / .env.example
```

**Aturan pemisahan lapisan:**

- `models/` = bentuk tabel saja. Tidak ada logika bisnis.
- `schemas/` = kontrak API. Sengaja tidak memakai model tabel sebagai response supaya
  kolom internal (mis. `password_hash`, `dpp_rate_basis_points`) tidak bocor.
- `routers/` = validasi akses + logika bisnis + query.
- `core/` = hal lintas-modul (auth, config, dependency).

---

## 4. Konfigurasi & Environment

Semua konfigurasi dibaca dari `backend/.env` lewat `app/core/config.py`. Jalur file `.env`
dihitung absolut (`BACKEND_DIR = parents[2]`), jadi aplikasi bisa dijalankan dari direktori mana pun.

| Variabel | Default | Keterangan |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://pajaksim:pajaksim_dev@localhost:5432/pajaksim` | Satu-satunya tempat connection string |
| `APP_ENV` | `development` | `production` mengaktifkan pemeriksaan keamanan |
| `SECRET_KEY` | `change-me-in-production` | Kunci HMAC JWT |
| `JWT_ALGORITHM` | `HS256` | |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | |
| `CORS_ORIGINS` | `http://localhost:3000` | Dipisah koma; disimpan sebagai `str` agar pydantic tidak mencoba JSON-decode |
| `AUTO_SEED` | `true` | Seed otomatis saat startup jika DB kosong |

**Properti turunan:**

- `settings.cors_origins` → `list[str]` hasil split & strip.
- `settings.is_production` → `APP_ENV.lower() == "production"`.

**`validate_runtime_safety()`** dipanggil di `create_app()`. Di produksi aplikasi
**menolak start** bila:

- `SECRET_KEY` masih salah satu nilai di `UNSAFE_SECRET_KEYS`, atau
- `AUTO_SEED` masih `true`.

---

## 5. Bootstrap Aplikasi (`app/main.py`)

```python
API_PREFIX = "/api/v1"
```

Alur `create_app()`:

1. `settings.validate_runtime_safety()` — gagal cepat pada konfigurasi produksi tidak aman.
2. Buat `FastAPI(title="Simulator Pajak — API", version="0.1.0", lifespan=lifespan)`.
3. Pasang `CORSMiddleware` (`allow_credentials=True`, semua metode & header).
4. Daftarkan router dengan prefix `/api/v1`:
   `auth`, `tenants`, `users`, `classes`, `bp21`, `bp26`, `tarif_pajak`, `dashboard`.
5. Endpoint `GET /health` (di luar prefix) → `{"status": "ok"}`, tag `meta`.

**Lifespan:** bila `AUTO_SEED=true`, `seed_if_empty()` dipanggil sekali saat startup.

**Sesi database:** `app/db.py` membuat satu `engine` global dengan `pool_pre_ping=True`
(menghindari error koneksi basi setelah Postgres restart di dev). `get_session()` adalah
generator dependency FastAPI yang membuka `Session` per request.

---

## 6. Lapisan Inti (`app/core/`)

### `security.py`

| Fungsi | Keterangan |
|---|---|
| `hash_password(plain)` | bcrypt via passlib `CryptContext` |
| `verify_password(plain, hashed)` | |
| `create_access_token(user_id, role, tenant_id)` | Claim: `sub` (string user id), `role`, `tenant_id`, `iat`, `exp`, `type="access"` |
| `create_refresh_token(user_id)` | Claim: `sub`, `iat`, `exp`, `type="refresh"` |
| `decode_token(token)` | Melempar `jwt.PyJWTError` bila gagal |

Konstanta `TOKEN_TYPE_ACCESS = "access"`, `TOKEN_TYPE_REFRESH = "refresh"`. Klaim `type`
mencegah refresh token dipakai sebagai access token.

### `deps.py`

- `SessionDep = Annotated[Session, Depends(get_session)]`
- `get_current_user(...)` → `CurrentUser`. Alurnya: ambil bearer token (`HTTPBearer(auto_error=False)`
  supaya bentuk error 401 konsisten) → decode → pastikan `type == "access"` → ambil `sub` →
  load `User` → tolak bila tidak ada atau `is_active=False`. Semua kegagalan menghasilkan
  401 dengan detail Indonesia: *"Kredensial tidak valid atau sesi berakhir"*.
- `require_roles(*roles)` → dependency factory. Bila role tidak termasuk, 403
  *"Anda tidak memiliki akses ke sumber daya ini"*.
- `tenant_filter(query, user, model)` → generik; superadmin melihat semua, peran lain
  di-`where(model.tenant_id == user.tenant_id)`. Disentralisasi supaya isolasi tenant
  menjadi satu panggilan eksplisit di setiap call site — **jangan pernah mengandalkan
  frontend untuk memfilter**.

---

## 7. Model Data & Skema Database

### Diagram relasi

```
tenants ─┬──< users ─────< enrollments >───── classes
         │      │                               │
         │      └── (guru_id) ──────────────────┘
         │
         ├──< classes
         └──< withholding_slips >── users (siswa_id, created_by_id)
                                 └── classes (class_id, nullable)

tarif_ptkp        (referensi global, tanpa tenant)
tarif_progresif   (referensi global, tanpa tenant)
```

### `TimestampMixin` (`models/base.py`)

Menambahkan `created_at` & `updated_at` bertipe `TIMESTAMPTZ`, `server_default=now()`,
`updated_at` memakai `onupdate=now()`. Implementasi memakai `sa_type` + `sa_column_kwargs`
(**bukan** instance `sa_column` bersama) karena satu objek `Column` tidak bisa dipasang ke
lebih dari satu tabel.

### `tenants`

| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | PK | |
| `name` | varchar(150), index | |
| `slug` | varchar(80), **unique**, index | Dipakai saat login sebagai "Kode Sekolah/Lembaga" |
| `type` | enum `tenant_type` | `smk` \| `kampus` \| `lembaga` |
| `is_active` | bool, default true | |
| `created_at`, `updated_at` | timestamptz | |

### `users`

| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | PK | |
| `tenant_id` | FK → tenants, **nullable**, `ON DELETE CASCADE` | `NULL` hanya untuk superadmin |
| `email` | varchar(255), index | Disimpan lowercase |
| `password_hash` | varchar(255) | |
| `full_name` | varchar(150) | |
| `role` | enum `user_role` | `superadmin` \| `admin` \| `guru` \| `siswa` |
| `is_active` | bool, default true | |

Constraint:

- `uq_users_tenant_email` — unik `(tenant_id, email)`. Email **boleh** sama di tenant berbeda.
- `uq_users_superadmin_email` — partial unique index pada `email` `WHERE tenant_id IS NULL`.
  Diperlukan karena SQL menganggap `NULL` di composite unique sebagai nilai berbeda.

### `classes` (model `SchoolClass`)

Nama tabel `classes` karena `class` adalah reserved word Python.

| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | PK | |
| `tenant_id` | FK → tenants, `CASCADE`, index | |
| `name` | varchar(100) | mis. "XII Akuntansi 1" |
| `academic_year` | varchar(9) | mis. "2026/2027" |
| `guru_id` | FK → users, `RESTRICT`, index | Guru tidak bisa dihapus bila masih memegang kelas |

### `enrollments`

| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | PK | |
| `class_id` | FK → classes, `CASCADE`, index | |
| `siswa_id` | FK → users, `CASCADE`, index | |

Constraint `uq_enrollments_class_siswa` — unik `(class_id, siswa_id)`.

### `withholding_slips` (model `WithholdingSlip`)

**Satu tabel untuk semua jenis bupot.** BP21 (PPh 21, penerima dalam negeri) dan BP26
(PPh 26, penerima luar negeri) berbagi struktur & siklus hidup yang identik; keduanya
hanya berbeda pada *nilai data*. Kolom `slip_type` murni diskriminator — semua kolom bisa
dipakai semua tipe.

**Identitas & kepemilikan**

| Kolom | Tipe | Keterangan |
|---|---|---|
| `id` | PK | |
| `slip_type` | enum `slip_type`, index | `bp21` \| `bp26` |
| `tenant_id` | FK → tenants, `CASCADE`, index | |
| `class_id` | FK → classes, `SET NULL`, nullable, index | |
| `siswa_id` | FK → users, `CASCADE`, index | Pemilik dokumen |
| `created_by_id` | FK → users, `RESTRICT`, index | Bisa berbeda dari siswa (admin/guru buat atas nama siswa) |

**Status & siklus hidup**

| Kolom | Tipe | Keterangan |
|---|---|---|
| `status` | enum `slip_status`, index, default `draft` | `draft` \| `issued` \| `invalid` |
| `withholding_number` | varchar(40), **unique**, nullable, index | Digenerate saat terbit |
| `issued_at` | timestamptz, nullable | |
| `invalid_reason` | varchar(500), nullable | |
| `spt_flag` | enum `slip_spt_flag`, nullable, index | Hanya untuk dokumen `issued` |
| `electronic_signature_status` | varchar(40), default `"not_signed"` | Jadi `"signed"` saat terbit |

**Masa pajak**

| Kolom | Tipe | Keterangan |
|---|---|---|
| `tax_month` | int 1–12, index | |
| `tax_year` | int 2020–2100, index | |

**Pemotong**: `withholder_npwp` (32), `withholder_name` (150), `withholder_nitku` (32) — semua nullable.

**Penerima penghasilan**: `recipient_identity_number` (32, wajib), `recipient_name` (150, wajib),
`recipient_address` (255), `recipient_nitku` (32), `ptkp_status` (10, mis. `"TK/0"`).

**Objek pajak**: `tax_type` (30), `tax_object_code` (30, wajib), `tax_object_name` (150),
`income_type` (150), `tax_nature` (enum, wajib: `final`/`non_final`),
`tax_facility` (enum, default `none`).

**Nilai uang** — semua rupiah disimpan **integer**, semua persen disimpan **basis points**
(10000 = 100,00%) agar aritmatika presisi tetap eksak:

| Kolom | Keterangan |
|---|---|
| `previous_gross_income` | Penghasilan bruto sebelumnya |
| `gross_income` | Penghasilan bruto |
| `dpp` | Dasar Pengenaan Pajak — **dihitung server** |
| `dpp_rate_basis_points` | Default 10000 (=100%) |
| `rate_basis_points` | mis. 500 = 5,00% |
| `income_tax` | PPh — **dihitung server** |
| `kap_kjs` | varchar(20), mis. `"411121-100"` |

**Tax treaty (P3B)** — dipakai BP26: `negara_treaty` (5), `pasal_treaty` (20),
`nomor_skd` (100), `tarif_treaty_basis_points` (0–10000, nullable).

**Dokumen dasar**: `document_type` (60), `document_number` (60), `document_date` (date), `document_nitku` (32).

**Penilaian**: `score` (0–100, nullable), `teacher_feedback` (500, nullable).

### `tarif_ptkp` (model `TierPtkp`)

Data referensi global (tanpa `tenant_id`, tanpa timestamp).

| Kolom | Keterangan |
|---|---|
| `id` | PK |
| `status_kode` | varchar(10), index — `"TK/0"`, `"K/1"`, … |
| `jumlah_ptkp` | int ≥ 0 (rupiah setahun) |
| `tahun_pajak` | int, index |
| `keterangan` | varchar(100), nullable |
| `is_active` | bool, default true |

Unik `(status_kode, tahun_pajak)`.

### `tarif_progresif` (model `TarifProgresifPasal17`)

| Kolom | Keterangan |
|---|---|
| `id` | PK |
| `batas_bawah` | **BIGINT** ≥ 0 |
| `batas_atas` | **BIGINT**, nullable — `NULL` = tak terbatas (lapisan teratas) |
| `persentase_basis_points` | int 0–10000 |
| `tahun_pajak` | int, index |
| `keterangan` | varchar(100), nullable |
| `is_active` | bool, default true |

BIGINT diperlukan karena lapisan tertinggi Pasal 17 dimulai di 5.000.000.000 (melebihi
INT4). Ini isi migrasi `25c389d80451`.

---

## 8. Enumerasi

Semua di `app/models/enums.py`, semuanya `StrEnum` (serialisasi JSON sebagai string,
disimpan sebagai enum native PostgreSQL).

| Enum | Nama tipe PG | Nilai |
|---|---|---|
| `TenantType` | `tenant_type` | `smk`, `kampus`, `lembaga` |
| `Role` | `user_role` | `superadmin`, `admin`, `guru`, `siswa` |
| `SlipType` | `slip_type` | `bp21`, `bp26` |
| `SlipStatus` | `slip_status` | `draft`, `issued`, `invalid` |
| `SlipTaxNature` | `slip_tax_nature` | `final`, `non_final` |
| `SlipTaxFacility` | `slip_tax_facility` | `none`, `dtp`, `skb`, `rate_0` |
| `SlipSptFlag` | `slip_spt_flag` | `reported_in_spt`, `objection_in_progress`, `objection_completed`, `objection_rejected_formal`, `objection_withdrawal_review`, `objection_withdrawal_accepted`, `spt_audited`, `spt_legal_process` |

> Menambah nilai enum di PostgreSQL memerlukan migrasi Alembic eksplisit
> (`ALTER TYPE ... ADD VALUE`) — autogenerate tidak menangkapnya.

---

## 9. Model RBAC & Multi-Tenancy

### Prinsip

1. **Tenancy dipaksakan di server, selalu.** Setiap query yang menyentuh data tenant harus
   melewati filter tenant. Parameter `tenant_id` dari klien hanya dihormati untuk superadmin.
2. **Satu peran per user** (kolom enum, bukan tabel many-to-many).
3. **Superadmin = `tenant_id IS NULL`.** Ini invarian; kode di banyak tempat mengandalkannya.
4. Setiap tabel tenant-scoped di masa depan **wajib** punya `tenant_id` dan mengikuti pola yang sama.

### Matriks akses ringkas

| Sumber daya | superadmin | admin | guru | siswa |
|---|---|---|---|---|
| `/tenants` (semua) | ✅ | ❌ | ❌ | ❌ |
| `/users` list | semua/pilih tenant | tenantnya saja | ❌ | ❌ |
| `/users` create | peran apa pun | hanya guru & siswa, tenantnya | ❌ | ❌ |
| `/users/{id}` patch | siapa pun | user di tenantnya | ❌ | ❌ |
| `/classes` list | ❌ (tidak termasuk role guard) | semua kelas tenant | kelas yang diajar | kelas yang diikuti |
| `/classes` create & enroll | ❌ | ✅ | ❌ | ❌ |
| bupot: baca | semua | tenantnya | kelas yang diajar | miliknya |
| bupot: buat/ubah/hapus/terbit | ✅ | ✅ (tenant) | ✅ (wajib pilih kelas) | ✅ (miliknya) |
| bupot: review / invalidate / spt-flag | ✅ | ✅ | ✅ | ❌ |
| `/tarif-pajak` baca | ✅ | ✅ | ✅ | ✅ |
| `/tarif-pajak` tulis | ✅ | ❌ | ❌ | ❌ |

> Catatan: router `/classes` sengaja **tidak** memasukkan `superadmin` di role guard —
> penjelajahan kelas bersifat lokal-institusi.

### Implementasi filter bupot (`apply_access_filters`)

```python
superadmin → tanpa filter
admin      → WHERE tenant_id = user.tenant_id
siswa      → WHERE siswa_id  = user.id
guru       → WHERE class_id IN (SELECT id FROM classes WHERE guru_id = user.id)
```

Padanan untuk objek tunggal adalah `can_read_slip()` dengan logika yang sama. Dokumen yang
tidak boleh dibaca dijawab **404**, bukan 403, agar keberadaannya tidak bocor.

---

## 10. Referensi API Lengkap

Base URL: `http://localhost:8000`. Semua endpoint bisnis diawali `/api/v1`.
Autentikasi: header `Authorization: Bearer <access_token>`.

### Meta

| Metode | Path | Auth | Keterangan |
|---|---|---|---|
| GET | `/health` | — | `{"status": "ok"}` |
| GET | `/docs` | — | Swagger UI |
| GET | `/openapi.json` | — | Skema OpenAPI |

### Auth — `/api/v1/auth`

| Metode | Path | Auth | Body | Respons |
|---|---|---|---|---|
| POST | `/login` | — | `LoginRequest{email, password, tenant_slug?}` | `TokenPair{access_token, refresh_token, token_type}` |
| POST | `/refresh` | — | `RefreshRequest{refresh_token}` | `AccessTokenResponse{access_token, token_type}` |
| GET | `/me` | ✅ | — | `MeResponse{id, email, full_name, role, tenant_id, tenant_name, is_active}` |

**Resolusi login (`_resolve_login_user`)** — penting untuk frontend:

1. Bila `tenant_slug` dikirim → cari user di tenant tersebut. Tidak ketemu → **401**.
2. Bila tidak dikirim → superadmin (`tenant_id IS NULL`) diprioritaskan.
3. Bila bukan superadmin → cari email di seluruh tenant:
   - 0 hasil → **401** *"Email atau kata sandi salah"*
   - 1 hasil → dipakai
   - > 1 hasil → **409** *"Email terdaftar di beberapa institusi. Sertakan Kode Sekolah/Lembaga."*
     → frontend harus menampilkan input Kode Sekolah/Lembaga lalu retry.

Email di-lowercase sebelum pencarian. User non-aktif ditolak 401.

### Tenants — `/api/v1/tenants` (superadmin saja)

| Metode | Path | Body | Respons |
|---|---|---|---|
| GET | `` | — | `list[TenantRead]` (urut `id`) |
| POST | `` | `TenantCreate{name, slug, type}` | 201 `TenantRead`; slug duplikat → **409** |
| PATCH | `/{tenant_id}` | `TenantUpdate{name?, type?, is_active?}` | `TenantRead`; tidak ada → **404** |

> Tidak ada endpoint DELETE tenant.

### Users — `/api/v1/users` (superadmin + admin)

| Metode | Path | Query / Body | Respons |
|---|---|---|---|
| GET | `` | `role?`, `tenant_id?`, `page=1`, `size=20` (1–100) | `UserListResponse{items, total, page, size}` |
| POST | `` | `UserCreate{email, password(min 8), full_name, role, tenant_id?}` | 201 `UserRead` |
| PATCH | `/{user_id}` | `UserUpdate{full_name?, password?, is_active?}` | `UserRead` |

Aturan:

- Admin: parameter `tenant_id` **diabaikan**, selalu dipaksa ke tenantnya sendiri.
- Admin hanya boleh membuat `guru` / `siswa` → selain itu **403**.
- Membuat `superadmin` memaksa `tenant_id = None`; peran lain wajib `tenant_id` valid → **422**.
- Email duplikat dalam tenant yang sama → **409**.
- Admin mengubah user tenant lain → **403**.
- Tidak ada endpoint DELETE user (nonaktifkan lewat `is_active=false`).

Validasi email memakai pola ringan `^[^@\s]+@[^@\s]+\.[^@\s]+$`, **bukan** `EmailStr`
(alasan di §19).

### Classes — `/api/v1/classes` (admin, guru, siswa)

| Metode | Path | Peran | Keterangan |
|---|---|---|---|
| GET | `` | admin/guru/siswa | Admin: semua kelas tenant. Guru: kelas yang diajar. Siswa: kelas yang diikuti |
| POST | `` | admin | `ClassCreate{name, academic_year, guru_id}`; `guru_id` harus guru di tenant yang sama → **422** |
| GET | `/{class_id}` | admin/guru/siswa | `ClassDetail` (termasuk `guru` & daftar `students`) |
| POST | `/{class_id}/enrollments` | admin | `EnrollmentCreate{siswa_id}` → 201 `ClassDetail`; idempoten (enrol ganda diabaikan) |

Detail akses `GET /{class_id}`: kelas di tenant lain → **404**; guru bukan pemilik kelas →
**403** *"Bukan kelas Anda"*; siswa tidak terdaftar → **403**.

### Dashboard — `/api/v1/dashboard/summary`

Satu endpoint, respons berbentuk sesuai peran (`DashboardSummary`, semua field opsional):

| Peran | Field terisi |
|---|---|
| superadmin | `tenants`, `users`, `classes` (hitungan global) |
| admin | `guru`, `siswa`, `classes` (dalam tenant) |
| guru | `classes` (yang diajar), `siswa` (distinct dari enrollment kelasnya) |
| siswa | `classes` (jumlah enrollment) |

### Tarif Pajak — `/api/v1/tarif-pajak`

Baca: semua peran. Tulis: **superadmin saja**.

| Metode | Path | Keterangan |
|---|---|---|
| GET | `/ptkp` | Query `tahun_pajak?`, `is_active?`, `page=1`, `size=50` (1–100). Urut `tahun_pajak, status_kode` |
| POST | `/ptkp` | 201; `(status_kode, tahun_pajak)` duplikat → **409** |
| PATCH | `/ptkp/{ptkp_id}` | Validasi keunikan ulang setelah patch |
| DELETE | `/ptkp/{ptkp_id}` | 204 |
| GET | `/progresif` | Query sama; urut `tahun_pajak, batas_bawah` |
| POST | `/progresif` | 201 |
| PATCH | `/progresif/{bracket_id}` | |
| DELETE | `/progresif/{bracket_id}` | 204 |

Validasi bracket (`_validate_bracket`):

- `batas_atas <= batas_bawah` → **422**.
- Bracket **aktif** tidak boleh tumpang tindih dengan bracket aktif lain di tahun yang sama
  → **409**. `batas_atas = NULL` diperlakukan sebagai `10**30` untuk pemeriksaan overlap.
- Bracket non-aktif dilewati pemeriksaan overlap.

### BP21 / BP26

Lihat §11 — set endpoint identik, keduanya dibangun dari factory yang sama.

---

## 11. Modul e-Bupot (BP21 / BP26)

`app/routers/slips.py` mendefinisikan `make_slip_router(slip_type, label)` yang menghasilkan
set endpoint lengkap untuk satu jenis bupot. Di akhir file:

```python
bp21_router = make_slip_router(SlipType.bp21, "BP21")
bp26_router = make_slip_router(SlipType.bp26, "BP26")
```

Menambah jenis bupot baru = satu nilai enum + satu pemanggilan factory (+ migrasi enum).

### Daftar endpoint (`{t}` = `bp21` atau `bp26`)

| Metode | Path | Peran | Keterangan |
|---|---|---|---|
| GET | `/api/v1/{t}` | semua | List berpaginasi + filter |
| GET | `/api/v1/{t}/summary` | semua | Hitungan `draft`/`issued`/`invalid`/`total` |
| GET | `/api/v1/{t}/import-template` | semua | Unduh template XML |
| GET | `/api/v1/{t}/export-csv` | semua | CSV per masa pajak |
| GET | `/api/v1/{t}/export-xml` | semua | XML seluruh field |
| POST | `/api/v1/{t}/import-xml` | semua | Upload XML (multipart `file`) |
| POST | `/api/v1/{t}` | semua | 201, buat draft + hitung PPh |
| POST | `/api/v1/{t}/bulk-issue` | semua | Terbitkan banyak draft |
| GET | `/api/v1/{t}/{slip_id}` | semua | Baca satu |
| PATCH | `/api/v1/{t}/{slip_id}` | semua | Ubah draft (hitung ulang) |
| DELETE | `/api/v1/{t}/{slip_id}` | semua | 204, hanya draft |
| POST | `/api/v1/{t}/{slip_id}/issue` | semua | Terbitkan |
| POST | `/api/v1/{t}/{slip_id}/cancel` | semua | Batalkan dokumen terbit |
| POST | `/api/v1/{t}/{slip_id}/invalidate` | super/admin/guru | Tandai tidak valid |
| PATCH | `/api/v1/{t}/{slip_id}/spt-flag` | super/admin/guru | Set flag SPT |
| PATCH | `/api/v1/{t}/{slip_id}/review` | super/admin/guru | Nilai & umpan balik |

> **Urutan rute penting.** `/summary`, `/import-template`, `/export-csv`, `/export-xml`,
> `/bulk-issue` didaftarkan **sebelum** `/{slip_id}` supaya tidak tertangkap sebagai path
> parameter integer.

### Filter pada `GET /{t}`

| Query | Tipe | Catatan |
|---|---|---|
| `status` | `SlipStatus` | alias dari parameter `status_filter` |
| `spt_flag` | `SlipSptFlag` | |
| `tenant_id` | int | **hanya dihormati untuk superadmin** |
| `class_id` | int | |
| `siswa_id` | int | diabaikan bila pemanggil `siswa` |
| `tax_year` | int 2020–2100 | |
| `tax_month` | int 1–12 | |
| `tax_facility` | `SlipTaxFacility` | |
| `page` | int ≥ 1, default 1 | |
| `size` | int 1–100, default 20 | |

Urutan: `id DESC`. Respons `SlipListResponse{items, total, page, size}`.

### Siklus hidup dokumen

```
        POST /{t}
           │
           ▼
      ┌─────────┐   issue / bulk-issue    ┌──────────┐
      │  draft  │ ──────────────────────► │  issued  │
      └─────────┘                         └──────────┘
        │     ▲                             │      │
 DELETE │     │ PATCH (ubah)         cancel │      │ spt-flag / review
        ▼     │                             ▼
      (hapus) │                        ┌──────────┐
              │       invalidate ─────►│ invalid  │
              └────────────────────────└──────────┘
                (invalidate berlaku dari status mana pun)
```

Aturan transisi:

| Aksi | Prasyarat | Efek | Bila salah status |
|---|---|---|---|
| `PATCH /{id}` | `draft` | Hitung ulang `dpp` & `income_tax` | **409** |
| `DELETE /{id}` | `draft` | Hapus baris | **409** |
| `POST /{id}/issue` | `draft` | `status=issued`, `issued_at=now(UTC)`, `electronic_signature_status="signed"`, generate `withholding_number` | **409** |
| `POST /{id}/cancel` | `issued` | `status=invalid`, `invalid_reason = reason` atau *"Dibatalkan oleh pemotong"* | **409** |
| `POST /{id}/invalidate` | apa pun | `status=invalid`, `invalid_reason` wajib (1–500 char) | — |
| `PATCH /{id}/spt-flag` | `issued` | Set/kosongkan `spt_flag` | **409** |
| `PATCH /{id}/review` | apa pun | Set `score` dan/atau `teacher_feedback` | — |

**Format nomor pemotongan:**

```
{LABEL}-{tax_year}{tax_month:02d}-{id:06d}     contoh: BP21-202601-000042
```

Kolom `withholding_number` unik di level database.

### Validasi cakupan tulis (`resolve_slip_scope`)

Dipanggil pada create dan update. Mengembalikan `(tenant_id, class_id, siswa_id)` tervalidasi.

1. Bila pemanggil `siswa` → `siswa_id` dipaksa jadi dirinya sendiri (nilai kiriman diabaikan).
2. `siswa_id` kosong untuk peran non-siswa → **422**.
3. `siswa_id` harus menunjuk user berperan `siswa` → **422**.
4. Non-superadmin & non-siswa: siswa harus satu tenant → **403** *"Akses lintas tenant ditolak"*.
5. Siswa tanpa tenant → **422**.
6. Bila `class_id` diisi:
   - kelas tidak ada / beda tenant → **404**
   - guru bukan pengajar kelas → **403**
   - siswa (pemanggil) tidak terdaftar di kelas → **403**
   - siswa target tidak terdaftar di kelas → **422**
7. Bila `class_id` kosong **dan** pemanggil `guru` → **422** (*"Guru wajib memilih kelas"*).
   Admin/superadmin/siswa boleh tanpa kelas.

### Skema request/response

- `SlipCreate` / `SlipUpdate` menerima **persen** (`dpp_percent`, `rate_percent`, `float` 0–100).
- `SlipRead` mengembalikan **persen** juga (`dpp_percent`, `rate_percent`), dikonversi dari
  basis points oleh `slip_to_read()`. Kolom `*_basis_points` internal tidak diekspos —
  kecuali `tarif_treaty_basis_points` yang memang bagian kontrak.
- Field yang dihitung server (`dpp`, `income_tax`, `status`, `withholding_number`,
  `issued_at`, `electronic_signature_status`) **tidak** bisa dikirim klien.

---

## 12. Mesin Perhitungan Pajak

Semua uang adalah integer rupiah; semua tarif adalah basis points. Konversi memakai
`Decimal` dengan `ROUND_HALF_UP` — tidak ada `float` pada jalur uang.

### Fungsi dasar

```python
percent_to_basis_points(5.0)        # 500
basis_points_to_percent(500)        # 5.0
calculate_dpp(gross, dpp_bp)        # gross * dpp_bp / 10000, dibulatkan
calculate_income_tax(dpp, rate_bp, facility)
```

`calculate_income_tax` mengembalikan **0** bila fasilitas `skb` atau `rate_0`.

### Pemilihan metode (`_resolve_income_tax`)

Urutan prioritas:

1. **Fasilitas `skb` / `rate_0` → PPh = 0.** Selalu menang.
2. **BP21 + `non_final`** → coba tarif progresif Pasal 17 (lihat di bawah).
   Bila berhasil, hasilnya dipakai.
3. **BP26 + `non_final` + `negara_treaty` terisi + `tarif_treaty_basis_points` terisi**
   → `dpp × tarif_treaty_bp / 10000`.
4. **Fallback** → `dpp × rate_bp / 10000`.

> Fasilitas `dtp` dan `none` **tidak** mengubah perhitungan saat ini (`dtp` hanya penanda).

### Tarif progresif BP21 (`_calculate_bp21_progressive_tax`)

Mengembalikan `None` (→ jatuh ke fallback) bila:

- `ptkp_status` kosong, **atau**
- tidak ada baris `tarif_ptkp` aktif untuk `(status_kode, tahun_pajak)`, **atau**
- tidak ada bracket `tarif_progresif` aktif untuk tahun tersebut.

Algoritma:

```
pkp        = max(0, gross_income * 12 - jumlah_ptkp)      # setahunkan lalu kurangi PTKP
annual_tax = Σ  atas setiap bracket (urut batas_bawah):
                upper = batas_atas ?? pkp
                chunk = min(pkp, upper) - batas_bawah
                skip bila chunk <= 0
                annual_tax += chunk * persentase_basis_points / 10000
hasil      = annual_tax // 12                             # kembali ke bulanan
```

Catatan: `pkp` dihitung dari `gross_income` bulanan yang disetahunkan (×12), lalu pajak
tahunan dibagi 12 dengan pembagian integer (`//`).

**Contoh** — gaji Rp 10.000.000/bulan, PTKP `TK/0` (54.000.000), tahun 2026:

```
pkp        = 120.000.000 - 54.000.000 = 66.000.000
lapis 1    : 0–60jt      @5%   → 3.000.000
lapis 2    : 60jt–250jt  @15%  → 6.000.000 × 15% = 900.000
annual_tax = 3.900.000
bulanan    = 325.000
```

### Nilai referensi bawaan (dari seed, tahun 2024–2026)

**PTKP:**

| Status | Jumlah |
|---|---|
| TK/0 | 54.000.000 |
| TK/1 | 58.500.000 |
| TK/2 | 63.000.000 |
| TK/3 | 67.500.000 |
| K/0 | 58.500.000 |
| K/1 | 63.000.000 |
| K/2 | 67.500.000 |
| K/3 | 72.000.000 |

**Pasal 17:**

| Batas bawah | Batas atas | Tarif |
|---|---|---|
| 0 | 60.000.000 | 5% |
| 60.000.000 | 250.000.000 | 15% |
| 250.000.000 | 500.000.000 | 25% |
| 500.000.000 | 5.000.000.000 | 30% |
| 5.000.000.000 | — | 35% |

---

## 13. Import / Export

### Batasan

| Konstanta | Nilai | Berlaku pada |
|---|---|---|
| `MAX_IMPORT_BYTES` | 1.000.000 (≈1 MB) | Upload XML → **413** bila lebih |
| `MAX_IMPORT_ROWS` | 500 | Jumlah elemen baris → **422** bila lebih |
| `MAX_EXPORT_ROWS` | 5.000 | Export CSV/XML → **422** bila hasil melebihi |

### `GET /{t}/import-template`

Mengembalikan file XML contoh (`Content-Disposition: attachment`) berisi satu elemen baris
dengan nilai contoh yang berbeda per jenis:

| Field | BP21 | BP26 |
|---|---|---|
| `tax_type` | `PPh 21` | `PPh 26` |
| `tax_object_code` | `21-100-01` | `27-100-01` |
| `tax_nature` | `non_final` | `final` |
| `rate_percent` | `5` | `20` |
| `kap_kjs` | `411121-100` | `411127-100` |

### `POST /{t}/import-xml`

Multipart, field `file`. Struktur yang diterima:

```xml
<?xml version='1.0' encoding='utf-8'?>
<Bp21List>
  <Bp21>
    <tax_month>1</tax_month>
    <tax_year>2026</tax_year>
    <recipient_identity_number>1234567890123456</recipient_identity_number>
    <recipient_name>JOHN DOE</recipient_name>
    <tax_object_code>21-100-01</tax_object_code>
    <gross_income>10000000</gross_income>
    <dpp_percent>100</dpp_percent>
    <rate_percent>5</rate_percent>
    <!-- field lain opsional -->
  </Bp21>
</Bp21List>
```

Nama elemen root/baris: `Bp21List`/`Bp21`, `Bp26List`/`Bp26` (dari `label.capitalize()`).

Perilaku:

- Hanya 30 field pada tuple `_IMPORT_FIELDS` yang dibaca; elemen lain diabaikan.
- Elemen kosong / whitespace diperlakukan sebagai tidak dikirim.
- **Per-baris commit.** Baris gagal di-`rollback` sendiri, baris lain tetap tersimpan.
- Tidak ada elemen baris → **422**.
- XML tidak valid → **422**.

Respons `SlipImportResult`:

```json
{
  "total_rows": 3,
  "imported": 2,
  "failed": 1,
  "results": [
    {"row": 1, "success": true,  "id": 41,   "error": null},
    {"row": 2, "success": false, "id": null, "error": "gross_income: Input should be..."},
    {"row": 3, "success": true,  "id": 42,   "error": null}
  ]
}
```

Semua dokumen hasil import berstatus `draft`, PPh sudah dihitung.

### `GET /{t}/export-csv`

Filter: `status`, `tax_year`, `tax_month`. Kolom (mengikuti tampilan list e-Bupot):

```
Masa Pajak | Nomor Pemotongan | Status | Status Tanda Tangan Elektronik |
NITKU/Nomor Identitas Sub Unit Organisasi | Jenis Pajak | Kode Objek Pajak |
Nomor Identitas WP | Nama | Dasar Pengenaan Pajak (Rp) | Pajak Penghasilan (Rp) |
Fasilitas Pajak
```

`Masa Pajak` diformat `MM-YYYY`.

### `GET /{t}/export-xml`

Filter sama. Mengeluarkan **seluruh field `SlipRead`** sebagai elemen anak per dokumen.
Nilai `None` menghasilkan elemen kosong; enum diserialisasi lewat `.value`.

Kedua export memakai `apply_access_filters` — pengguna hanya bisa mengekspor yang boleh
dibacanya.

### `POST /{t}/bulk-issue`

Body: `{"ids": [1, 2, 3]}` (1–200 id). Setiap id diproses independen; id yang tidak
ditemukan/tidak boleh diakses atau bukan draft dilaporkan sebagai gagal tanpa menggagalkan
sisanya. Respons `SlipBulkIssueResult{issued, failed, results[]}` dengan `row` berisi id.

---

## 14. Migrasi Database (Alembic)

`alembic.ini` **tidak** menyimpan URL database. `alembic/env.py` menyuntikkannya dari
`settings.DATABASE_URL` dan memakai `SQLModel.metadata` sebagai `target_metadata`.
`compare_type=True` aktif di mode online maupun offline.

### Rantai revisi (linear)

```
2a9ed018d6a8  initial schema (tenants, users, classes, enrollments)
      ↓
8d2d6e9c4a10  add bp21 withholding slips
      ↓
4a3a0b885918  add bp21 coretax fields
      ↓
6c1f2b7d3e55  add bp26 withholding slips
      ↓
9e4f7a1c3b20  merge slips into withholding_slips   ← penggabungan jadi satu tabel
      ↓
b7c2d4e9a31f  add ptkp and progresif tariffs
      ↓
c8d3e5f0b42a  add treaty fields                     ← negara/pasal/skd/tarif treaty
      ↓
25c389d80451  change batas_bawah/batas_atas to BIGINT   (head)
```

### Perintah

```bash
uv run alembic upgrade head                       # terapkan
uv run alembic revision --autogenerate -m "pesan" # buat revisi baru
uv run alembic downgrade -1                       # mundur satu
uv run alembic current                            # revisi aktif
uv run alembic history                            # riwayat
```

**Hal yang tidak tertangkap autogenerate — tulis manual:**

- Penambahan nilai enum PostgreSQL (`ALTER TYPE ... ADD VALUE`).
- Partial index (mis. `uq_users_superadmin_email`).
- Migrasi data / backfill.
- Perubahan `ON DELETE` pada foreign key.

> `alembic/env.py` mengimpor `Enrollment, SchoolClass, Tenant, User, WithholdingSlip` dari
> `app.models`. Karena `app/models/__init__.py` juga mengimpor `TierPtkp` dan
> `TarifProgresifPasal17`, metadata tetap lengkap — tapi bila menambah model baru,
> pastikan model itu terdaftar di `app/models/__init__.py`.

---

## 15. Seed Data

`app/seed.py`. **Idempoten** — setiap insert dijaga pemeriksaan keberadaan.

Dua titik masuk:

| Fungsi | Dipakai oleh | Perilaku |
|---|---|---|
| `main()` / `python -m app.seed` | Manual | Seed penuh, cetak kredensial |
| `seed_if_empty()` | Startup app (`AUTO_SEED=true`) | Bila sudah ada user → hanya pastikan data tarif; bila kosong → seed penuh |

Isi seed:

- **Superadmin**: `super@pajaksim.local` (tenant `NULL`).
- **2 tenant**: `smkn1-pku` (SMK Negeri 1 Pekanbaru, tipe `smk`) dan `pcr`
  (Politeknik Caltex Riau, tipe `kampus`).
- Per tenant: 1 admin (`admin@{slug}.local`), 2 guru (`guru1/2@{slug}.local`),
  6 siswa (`siswa1..6@{slug}.local`).
- Per tenant: 2 kelas tahun ajaran `2026/2027` — "XII Akuntansi 1" (guru1, siswa 1–3) dan
  "XII Akuntansi 2" (guru2, siswa 4–6).
- **Data tarif**: PTKP & bracket Pasal 17 untuk tahun **2024, 2025, 2026**.

Password semua akun seed: `Password123!` (konstanta `SEED_PASSWORD`).

> Domain `.local` dipakai sengaja agar akun seed tidak routable.

---

## 16. Testing

```
tests/conftest.py        fixtures + guard database
tests/test_auth.py       login, refresh, me, RBAC dasar
tests/test_bp21.py       lifecycle & perhitungan BP21
tests/test_bp26.py       lifecycle, treaty, import/export BP26
tests/test_tarif_pajak.py  CRUD & validasi tarif
```

### Guard database

Tes berjalan terhadap PostgreSQL sungguhan (tidak ada fallback SQLite). `conftest.py`
**men-skip seluruh suite** — bukan gagal — bila:

1. Nama database di `DATABASE_URL` tidak mengandung substring `"test"`
   → *"DATABASE_URL must point to a dedicated test database."*
   Ini mencegah tes merusak database development.
2. Postgres tidak terjangkau (`OperationalError`).

Skip dipasang lewat `pytest_collection_modifyitems`.

Fixture `_ensure_seed` (session-scoped, autouse) memastikan data seed ada.
Helper: `login(client, email, password="Password123!", **extra)` dan
`auth_headers(client, email, **extra)`.

### Menjalankan

```bash
# sekali saja: buat DB tes
docker compose exec -T db psql -U pajaksim -c "CREATE DATABASE pajaksim_test OWNER pajaksim;"

# sekali saja / setelah migrasi baru
DATABASE_URL="postgresql+psycopg://pajaksim:pajaksim_dev@localhost:5432/pajaksim_test" \
  uv run alembic upgrade head

# jalankan
DATABASE_URL="postgresql+psycopg://pajaksim:pajaksim_dev@localhost:5432/pajaksim_test" \
  uv run pytest
```

Lint: `uv run ruff check .`

---

## 17. Konvensi Kode

**Bahasa**

- Kode, nama variabel, docstring, komentar: **Inggris**.
- Pesan error yang dilihat pengguna (`HTTPException.detail`): **Indonesia**.
- Nama field domain pajak boleh Indonesia bila itu istilah baku
  (`ptkp_status`, `negara_treaty`, `batas_bawah`, `tahun_pajak`, `keterangan`).

**Python**

- `from __future__ import annotations` di setiap modul.
- Type hints lengkap; sintaks union `X | None`.
- Keyword-only argument (`*`) untuk fungsi dengan banyak parameter sejenis.
- Ruff: E, F, I (import sorting), UP (pyupgrade), B (bugbear); line-length 100.
- Migrasi dikecualikan dari E501.

**Uang & persen**

- Rupiah = `int`. Persen = basis points (`int`, 10000 = 100%).
- Konversi selalu lewat `Decimal` + `ROUND_HALF_UP`. **Jangan pakai `float`** di jalur uang.

**HTTP**

| Kode | Dipakai untuk |
|---|---|
| 401 | Kredensial/token invalid, user non-aktif |
| 403 | Peran tidak berhak, akses lintas tenant, bukan kelas/kepemilikan Anda |
| 404 | Tidak ada **atau tidak boleh diakses** (menghindari kebocoran keberadaan data) |
| 409 | Konflik status siklus hidup, duplikat unik, login ambigu multi-tenant |
| 413 | File import terlalu besar |
| 422 | Validasi payload/relasi gagal |

**Pola router**

- Guard peran dideklarasikan sekali sebagai modul-level `Depends(require_roles(...))`
  lalu dipakai lewat `dependencies=[...]`.
- Rute statis didaftarkan sebelum rute dengan path parameter.
- Setelah `session.commit()`, selalu `session.refresh(obj)` sebelum mengembalikan.

---

## 18. Cara Menambah Fitur Baru

### Menambah tabel tenant-scoped

1. Buat model di `app/models/` — turunkan `TimestampMixin`, **wajib** ada
   `tenant_id: int = Field(sa_column=Column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True))`.
2. Ekspor di `app/models/__init__.py` (agar autogenerate melihatnya).
3. `uv run alembic revision --autogenerate -m "add <tabel>"`, lalu **baca & rapikan** hasilnya.
4. Buat skema di `app/schemas/` — pisahkan `Create`, `Update`, `Read`.
5. Buat router di `app/routers/`, pakai `require_roles(...)` dan filter tenant di **setiap** query.
6. Daftarkan router di `app/main.py`.
7. Tulis tes yang setidaknya menguji: happy path, penolakan lintas tenant, dan penolakan peran.

### Menambah jenis bupot baru (mis. BP23)

1. Tambah `bp23 = "bp23"` di `SlipType`.
2. Tulis migrasi manual: `ALTER TYPE slip_type ADD VALUE 'bp23'`.
3. Tambah `bp23_router = make_slip_router(SlipType.bp23, "BP23")` di `slips.py`.
4. Daftarkan di `main.py`.
5. Bila perhitungannya berbeda, tambahkan cabang di `_resolve_income_tax()`.

Bila BP23 butuh kolom yang belum ada, tambahkan ke `WithholdingSlip` sebagai nullable —
desainnya memang satu tabel lebar bersama.

### Menambah tahun tarif pajak

Tambahkan tahun ke `TARIF_YEARS` di `app/seed.py`, atau isi lewat endpoint
`POST /api/v1/tarif-pajak/ptkp` & `/progresif` sebagai superadmin.

---

## 19. Catatan Teknis & Utang Teknis

### Keputusan yang sudah diambil

| Topik | Keputusan | Alasan |
|---|---|---|
| bcrypt di-pin `<4.1` | Sengaja | passlib 1.7.4 membaca `bcrypt.__about__.__version__` yang dihapus di 4.1+. Hashing tetap jalan tanpa pin, tapi ada warning berisik saat startup |
| `str` alih-alih `EmailStr` | Sengaja | `email-validator` menolak TLD cadangan seperti `.local` yang dipakai akun seed non-routable. Divalidasi dengan pola ringan |
| Satu tabel `withholding_slips` | Sengaja | BP21 & BP26 identik secara struktural; diskriminator `slip_type` jauh lebih murah daripada dua tabel + dua router |
| `CORS_ORIGINS` sebagai `str` | Sengaja | Mencegah pydantic-settings mencoba JSON-decode nilai dari `.env` |
| 404 untuk resource tak berhak | Sengaja | Tidak membocorkan keberadaan dokumen milik tenant/siswa lain |
| Basis points untuk persen | Sengaja | Menjaga aritmatika rupiah tetap integer & eksak |

### Utang teknis / hal yang perlu diperhatikan

1. **Refresh token tidak bisa dicabut.** Tidak ada denylist maupun penyimpanan token —
   refresh token valid sampai kedaluwarsa (7 hari) meski user dinonaktifkan (pemeriksaan
   `is_active` terjadi saat refresh, jadi ini termitigasi sebagian, tapi tetap tidak ada revoke eksplisit).
2. **Penyimpanan token di frontend**: access token di memori, refresh token di
   `localStorage`. Diterima untuk dev; pertimbangkan cookie httpOnly sebelum produksi.
3. **Tidak ada rate limiting** pada `/auth/login` — brute force tidak dibatasi.
4. **Tidak ada endpoint DELETE** untuk tenant maupun user (hanya `is_active=false`).
5. **Import XML memakai `xml.etree.ElementTree`** yang rentan terhadap serangan entity
   expansion (billion laughs). Batas 1 MB mengurangi risiko tapi tidak menghilangkannya —
   pertimbangkan `defusedxml` bila import dibuka untuk masukan tidak tepercaya.
6. **Import melakukan commit per baris** — tidak ada atomisitas seluruh berkas (ini memang
   disengaja agar laporan per baris berguna, tapi perlu diketahui).
7. **Fasilitas `dtp` tidak memengaruhi perhitungan.** Saat ini hanya penanda.
8. **Tarif progresif jatuh diam-diam ke `rate_percent`** bila data PTKP/bracket tidak
   ditemukan. Tidak ada peringatan ke pengguna — pertimbangkan menampilkan sumber
   perhitungan pada respons.
9. **`invalidate` dan `review` menerima status apa pun**, termasuk `draft`. Bila aturan
   kelas menuntut lebih ketat, tambahkan pemeriksaan status.
10. **`tenant_filter()` di `deps.py` tidak dipakai router bupot** — modul slip memakai
    `apply_access_filters()` sendiri karena aturannya lebih kaya dari sekadar tenant.
11. **Tidak ada indeks komposit** pada `withholding_slips` untuk pola query umum
    (`slip_type, tenant_id, status, tax_year, tax_month`). Semua kolomnya sudah ter-index
    individual; evaluasi indeks komposit bila volume data bertambah.
12. **Tidak ada logging terstruktur / observability.** Hanya logger default Alembic/uvicorn.

### Peta jalan yang direncanakan

Faktur pajak simulasi · bupot PPh 23 · kode billing · SPT simulasi · pembuatan PDF ·
penilaian yang diperluas · laporan.

---

## Lampiran — Rangkuman Endpoint

```
GET    /health

POST   /api/v1/auth/login
POST   /api/v1/auth/refresh
GET    /api/v1/auth/me

GET    /api/v1/tenants
POST   /api/v1/tenants
PATCH  /api/v1/tenants/{tenant_id}

GET    /api/v1/users
POST   /api/v1/users
PATCH  /api/v1/users/{user_id}

GET    /api/v1/classes
POST   /api/v1/classes
GET    /api/v1/classes/{class_id}
POST   /api/v1/classes/{class_id}/enrollments

GET    /api/v1/dashboard/summary

GET    /api/v1/tarif-pajak/ptkp
POST   /api/v1/tarif-pajak/ptkp
PATCH  /api/v1/tarif-pajak/ptkp/{ptkp_id}
DELETE /api/v1/tarif-pajak/ptkp/{ptkp_id}
GET    /api/v1/tarif-pajak/progresif
POST   /api/v1/tarif-pajak/progresif
PATCH  /api/v1/tarif-pajak/progresif/{bracket_id}
DELETE /api/v1/tarif-pajak/progresif/{bracket_id}

# {t} ∈ {bp21, bp26}
GET    /api/v1/{t}
GET    /api/v1/{t}/summary
GET    /api/v1/{t}/import-template
GET    /api/v1/{t}/export-csv
GET    /api/v1/{t}/export-xml
POST   /api/v1/{t}/import-xml
POST   /api/v1/{t}
POST   /api/v1/{t}/bulk-issue
GET    /api/v1/{t}/{slip_id}
PATCH  /api/v1/{t}/{slip_id}
DELETE /api/v1/{t}/{slip_id}
POST   /api/v1/{t}/{slip_id}/issue
POST   /api/v1/{t}/{slip_id}/cancel
POST   /api/v1/{t}/{slip_id}/invalidate
PATCH  /api/v1/{t}/{slip_id}/spt-flag
PATCH  /api/v1/{t}/{slip_id}/review
```
