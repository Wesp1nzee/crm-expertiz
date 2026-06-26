"""add_document_status_and_upload_id

Revision ID: 2597f3b33acf
Revises: 82a6eee35c90
Create Date: 2026-06-04 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "2597f3b33acf"
down_revision: str | Sequence[str] | None = "82a6eee35c90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("status", sa.String(20), nullable=True),
    )

    op.add_column(
        "documents",
        sa.Column("upload_id", sa.Text(), nullable=True),
    )

    op.execute("UPDATE documents SET status = 'active' WHERE status IS NULL")

    op.alter_column(
        "documents",
        "status",
        existing_type=sa.String(20),
        nullable=False,
        server_default="pending",
    )

    op.create_index(
        op.f("ix_documents_status"),
        "documents",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_documents_company_id_status"),
        "documents",
        ["company_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_documents_status_created_at",
        "documents",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_documents_upload_id"),
        "documents",
        ["upload_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_documents_upload_id"), table_name="documents")
    op.drop_index("ix_documents_status_created_at", table_name="documents")
    op.drop_index(op.f("ix_documents_company_id_status"), table_name="documents")
    op.drop_index(op.f("ix_documents_status"), table_name="documents")

    op.drop_column("documents", "upload_id")
    op.drop_column("documents", "status")
