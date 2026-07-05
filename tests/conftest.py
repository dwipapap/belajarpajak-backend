"""Pytest fixtures.

Tests run against the Postgres instance in DATABASE_URL (per the spec — no sqlite
fallback). If the database is unreachable, the whole suite is skipped with a clear
message rather than failing noisily. Seed data is ensured once per session.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from app.db import engine
from app.main import app
from app.models.user import User
from app.seed import seed


def _db_reachable() -> bool:
    try:
        with Session(engine) as session:
            session.exec(select(User).limit(1))
        return True
    except OperationalError:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(),
    reason="Postgres not reachable via DATABASE_URL — see README to bootstrap the DB.",
)


@pytest.fixture(scope="session", autouse=True)
def _ensure_seed() -> None:
    """Make sure seed data exists so login fixtures resolve."""
    if not _db_reachable():
        return
    with Session(engine) as session:
        has_users = session.exec(select(User).limit(1)).first()
        if has_users is None:
            seed(session)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def login(client: TestClient, email: str, password: str = "Password123!", **extra) -> dict:
    """Helper: perform a login and return the JSON body (raises on non-200)."""
    payload = {"email": email, "password": password, **extra}
    resp = client.post("/api/v1/auth/login", json=payload)
    resp.raise_for_status()
    return resp.json()


def auth_headers(client: TestClient, email: str, **extra) -> dict[str, str]:
    tokens = login(client, email, **extra)
    return {"Authorization": f"Bearer {tokens['access_token']}"}
