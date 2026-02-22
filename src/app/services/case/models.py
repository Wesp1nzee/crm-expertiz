import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import UUID, DateTime, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.database.base import TenantBase

if TYPE_CHECKING:
    from src.app.services.client import Client
    from src.app.services.document import Document
    from src.app.services.mail import MailMessage
    from src.app.services.user import User


class CaseStatus(str, Enum):
    archive = "archive"
    in_work = "in_work"
    debt = "debt"
    executed = "executed"
    withdrawn = "withdrawn"
    cancelled = "cancelled"
    fssp = "fssp"


class Case(TenantBase):
    __tablename__ = "cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # Уникальный идентификатор дела

    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)

    number: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)  # Номер дела (уникальный)
    case_number: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)  # Номер производства (уникальный)
    authority: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    case_type: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    object_type: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    object_address: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    status: Mapped[CaseStatus] = mapped_column(
        SQLEnum(CaseStatus, native_enum=False),
        nullable=False,
        default=CaseStatus.in_work,
        index=True,
    )  # Статус дела

    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )  # ID назначенного эксперта

    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)  # Добавил индекс для фильтра по дате начала
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)  # Крайний срок
    completion_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)  # Добавил индекс для фильтра по дате завершения
    cost: Mapped[Decimal] = mapped_column(Numeric(precision=12, scale=2, asdecimal=True), nullable=False)  # Общая стоимость
    bank_transfer_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), server_default="0.00", default=Decimal("0.00"))  # Перевод на счёт
    cash_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), server_default="0.00", default=Decimal("0.00"))  # Наличные
    remaining_debt: Mapped[Decimal] = mapped_column(Numeric(12, 2), server_default="0.00", default=Decimal("0.00"))  # Оставшийся долг
    plaintiff: Mapped[str | None] = mapped_column(Text, index=True)  # истец
    defendant: Mapped[str | None] = mapped_column(Text, index=True)  # ответчик
    expert_painting: Mapped[str | None] = mapped_column(Text)  # Экспертная оценка/обследование
    archive_status: Mapped[str | None] = mapped_column(Text)  # Мб потом будет ссылка
    remarks: Mapped[str | None] = mapped_column(Text)  # Примечания
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)  # Добавил индекс для сортировки
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())  # Дата обновления
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)  # Дата логического удаления

    client: Mapped[Client] = relationship("Client", back_populates="cases")
    assigned_user: Mapped[User | None] = relationship("User", back_populates="cases")
    documents: Mapped[list[Document]] = relationship("Document", back_populates="case", cascade="all, delete-orphan")
    mail_messages: Mapped[list[MailMessage]] = relationship("MailMessage", back_populates="case")

    __table_args__ = (
        Index("ix_cases_company_id_client_id", "company_id", "client_id"),  # Дела клиента в рамках компании
        Index("ix_cases_company_id_status", "company_id", "status"),  # Фильтр по статусу в рамках компании
        Index("ix_cases_company_id_assigned_user_id_status", "company_id", "assigned_user_id", "status"),  # Рабочая очередь эксперта
        Index("ix_cases_company_id_deadline", "company_id", "deadline"),  # Контроль сроков в рамках компании
        Index("ix_cases_company_id_created_at", "company_id", "created_at"),  # Хронология дел
        Index("ix_cases_number_btree", "number"),  # Поиск по номеру дела
        Index("ix_cases_case_number_btree", "case_number"),  # Поиск по номеру производства
    )
