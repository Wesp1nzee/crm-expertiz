"""add legal_entity_type and new dates to cases

Revision ID: bbb57d4bb023
Revises: 5c79d35dd7d8
Create Date: 2026-05-20 13:05:03.716062

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "bbb57d4bb023"
down_revision: str | Sequence[str] | None = "5c79d35dd7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("legal_entity_type", sa.Enum("OOO", "IP", name="legalentitytype", native_enum=False), nullable=True))

    op.add_column("cases", sa.Column("additional_materials_date", sa.DateTime(timezone=True), nullable=True))
    op.add_column("cases", sa.Column("execution_date", sa.DateTime(timezone=True), nullable=True))

    op.execute("UPDATE cases SET legal_entity_type = 'OOO' WHERE legal_entity_type IS NULL")

    op.alter_column("cases", "legal_entity_type", nullable=False)

    op.create_index(op.f("ix_cases_additional_materials_date"), "cases", ["additional_materials_date"], unique=False)
    op.create_index(op.f("ix_cases_execution_date"), "cases", ["execution_date"], unique=False)
    op.create_index(op.f("ix_cases_legal_entity_type"), "cases", ["legal_entity_type"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_cases_legal_entity_type"), table_name="cases")
    op.drop_index(op.f("ix_cases_execution_date"), table_name="cases")
    op.drop_index(op.f("ix_cases_additional_materials_date"), table_name="cases")

    op.alter_column("cases", "legal_entity_type", nullable=True)

    op.drop_column("cases", "execution_date")
    op.drop_column("cases", "additional_materials_date")
    op.drop_column("cases", "legal_entity_type")
