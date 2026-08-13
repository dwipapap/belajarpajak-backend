"""FastAPI application factory: CORS, router registration, optional auto-seed."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routers import (
    auth,
    classes,
    dashboard,
    slips,
    tarif_pajak,
    tax_invoices,
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
        users.router,
        classes.router,
        slips.bp21_router,
        slips.bp23_router,
        slips.bp26_router,
        tax_invoices.router,
        tarif_pajak.router,
        dashboard.router,
    ):
        app.include_router(router, prefix=API_PREFIX)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
