"""Shared model mixins."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlmodel import Field, SQLModel


class TimestampMixin(SQLModel):
    """Adds UTC created_at / updated_at columns with server-side defaults.

    Uses ``sa_type`` + ``sa_column_kwargs`` (not a shared ``sa_column`` instance) so each
    concrete table gets its own Column — a single Column object cannot be attached to
    more than one table.
    """

    created_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": func.now(), "nullable": False},
    )
    updated_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={
            "server_default": func.now(),
            "onupdate": func.now(),
            "nullable": False,
        },
    )
