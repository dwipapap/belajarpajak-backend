"""FastAPI application factory: CORS, router registration, optional auto-seed."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.deps import require_module
from app.routers import (
    a1_a2,
    angsuran,
    assignments,
    audit,
    auth,
    billings,
    classes,
    dashboard,
    deposit,
    dokumen_saya,
    pbk,
    pengaturan,
    referensi,
    slips,
    tagihan,
    tarif_pajak,
    tax_input_invoices,
    tax_invoice_returns,
    tax_invoices,
    tax_returns,
    tenants,
    users,
)

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auto-seed on startup if enabled and the database is empty. Seeding is idempotent.
    if settings.AUTO_SEED:
        from app.seed import seed_if_empty

        seed_if_empty()
    yield


def create_app() -> FastAPI:
    settings.validate_runtime_safety()

    app = FastAPI(
        title="Simulator Pajak — API",
        version="0.1.0",
        description="Multi-tenant tax administration learning simulator API.",
        lifespan=lifespan,
        docs_url="/docs" if settings.api_docs_enabled else None,
        redoc_url="/redoc" if settings.api_docs_enabled else None,
        openapi_url="/openapi.json" if settings.api_docs_enabled else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router in (
        auth.router,
        tenants.router,
        pengaturan.router,
        users.router,
        classes.router,
        tax_returns.router,
        billings.router,
        dokumen_saya.router,
        audit.router,
        tarif_pajak.router,
        dashboard.router,
        referensi.router,
    ):
        app.include_router(router, prefix=API_PREFIX)

    gated_routers = (
        (assignments.router, "tugas"),
        (slips.bp21_router, "bp21"),
        (slips.bp26_router, "bp26"),
        (slips.bppu_router, "bppu"),
        (slips.bpmp_router, "bpmp"),
        (slips.bpnr_router, "bpnr"),
        (slips.sp_router, "sp"),
        (slips.cy_router, "cy"),
        (a1_a2.a1_router, "bpmp"),
        (a1_a2.a2_router, "bpmp"),
        (tax_invoices.router, "faktur_keluaran"),
        (tax_input_invoices.router, "faktur_masukan"),
        (tax_invoice_returns.router, "retur"),
        (tax_invoice_returns.keluaran_router, "retur"),
        (deposit.router, "deposit"),
        (pbk.router, "pbk"),
        (tagihan.router, "tagihan"),
        (angsuran.router, "angsuran"),
    )
    for router, module_key in gated_routers:
        app.include_router(
            router,
            prefix=API_PREFIX,
            dependencies=[Depends(require_module(module_key))],
        )

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()

