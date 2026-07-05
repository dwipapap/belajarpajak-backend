"""Database engine and session management."""

from __future__ import annotations

from collections.abc import Generator

from sqlmodel import Session, create_engine

from app.core.config import settings

# `pool_pre_ping` avoids stale-connection errors after the DB restarts (common in local dev).
engine = create_engine(settings.DATABASE_URL, echo=False, pool_pre_ping=True)


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a scoped database session."""
    with Session(engine) as session:
        yield session
