"""FastAPI application factory: CORS, router registration, optional auto-seed."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routers import auth, bp21, bp26, classes, dashboard, tenants, users

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auto-seed on startup if enabled and the database is empty. Seeding is idempotent.
    if settings.AUTO_SEED:
        from app.seed import seed_if_empty

        seed_if_empty()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Simulator Pajak — API",
        version="0.1.0",
        description="Phase 1 foundation: auth, tenancy, RBAC, dashboards.",
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
        bp21.router,
        bp26.router,
        dashboard.router,
    ):
        app.include_router(router, prefix=API_PREFIX)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
