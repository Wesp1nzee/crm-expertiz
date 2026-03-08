"""add case_experts many-to-many, remove assigned_user_id

Revision ID: cadd8090f399
Revises: 0e07182b84de
Create Date: 2026-03-08 08:26:39.145832

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "cadd8090f399"
down_revision: str | Sequence[str] | None = "0e07182b84de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Создаём таблицу case_experts
    op.create_table(
        "case_experts",
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], name=op.f("fk_case_experts_case_id_cases"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_case_experts_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("case_id", "user_id", name=op.f("pk_case_experts")),
    )
    op.create_index("ix_case_experts_user_id", "case_experts", ["user_id"])

    # 2. Переносим существующие данные ДО удаления колонки
    op.execute("""
        INSERT INTO case_experts (case_id, user_id)
        SELECT id, assigned_user_id
        FROM cases
        WHERE assigned_user_id IS NOT NULL
    """)

    # 3. Удаляем старую колонку
    op.drop_index(op.f("ix_cases_assigned_user_id"), table_name="cases")
    op.drop_index(op.f("ix_cases_company_id_assigned_user_id_status"), table_name="cases")
    op.drop_constraint(op.f("fk_cases_assigned_user_id_users"), "cases", type_="foreignkey")
    op.drop_column("cases", "assigned_user_id")


def downgrade() -> None:
    # 1. Возвращаем колонку (пока NULL, данные зальём ниже)
    op.add_column("cases", sa.Column("assigned_user_id", sa.UUID(), autoincrement=False, nullable=True))

    op.execute("""
        UPDATE cases c
        SET assigned_user_id = ce.user_id
        FROM (
            SELECT DISTINCT ON (case_id) case_id, user_id
            FROM case_experts
            ORDER BY case_id, assigned_at ASC
        ) ce
        WHERE c.id = ce.case_id
    """)

    # 3. Возвращаем FK и индексы
    op.create_foreign_key(op.f("fk_cases_assigned_user_id_users"), "cases", "users", ["assigned_user_id"], ["id"], ondelete="SET NULL")
    op.create_index(op.f("ix_cases_company_id_assigned_user_id_status"), "cases", ["company_id", "assigned_user_id", "status"], unique=False)
    op.create_index(op.f("ix_cases_assigned_user_id"), "cases", ["assigned_user_id"], unique=False)

    op.drop_index("ix_case_experts_user_id", table_name="case_experts")
    op.drop_table("case_experts")
