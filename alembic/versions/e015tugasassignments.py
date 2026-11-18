"""Tugas (assignments) and assignment_submissions tables.

Revision ID: e015tugasassignments
Revises: e014taxinvoicereturn
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e015tugasassignments"
down_revision: str | None = "e014taxinvoicereturn"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    status_enum = postgresql.ENUM(
        "draft", "published", "closed", name="assignment_status", create_type=False
    )
    status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "assignments",
        *_ts(),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False, server_default=""),
        sa.Column("doc_type", sa.String(length=40), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", status_enum, nullable=False, server_default="draft"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    for col in ("tenant_id", "class_id", "created_by", "doc_type", "due_at", "status"):
        op.create_index(op.f(f"ix_assignments_{col}"), "assignments", [col])

    op.create_table(
        "assignment_submissions",
        *_ts(),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("assignment_id", sa.Integer(), nullable=False),
        sa.Column("siswa_id", sa.Integer(), nullable=False),
        sa.Column("doc_id", sa.Integer(), nullable=False),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("is_late", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "auto_checks",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("final_score", sa.Integer(), nullable=True),
        sa.Column("teacher_feedback", sa.String(length=500), nullable=True),
        sa.Column("graded_by", sa.Integer(), nullable=True),
        sa.Column("graded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["assignment_id"], ["assignments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["siswa_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["graded_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "assignment_id", "siswa_id", name="uq_assignment_submission_siswa"
        ),
    )
    for col in ("assignment_id", "siswa_id", "doc_id", "graded_by"):
        op.create_index(
            op.f(f"ix_assignment_submissions_{col}"), "assignment_submissions", [col]
        )


def downgrade() -> None:
    op.drop_table("assignment_submissions")
    op.drop_table("assignments")
    status_enum = postgresql.ENUM(
        "draft", "published", "closed", name="assignment_status", create_type=False
    )
    status_enum.drop(op.get_bind(), checkfirst=True)
