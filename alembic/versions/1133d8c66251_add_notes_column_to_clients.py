"""add_notes_column_to_clients

Revision ID: 1133d8c66251
Revises: 70c4f176c9cf
Create Date: 2026-04-03 08:25:54.947334

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1133d8c66251"
down_revision: str | Sequence[str] | None = "70c4f176c9cf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("clients", sa.Column("notes", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("clients", "notes")
