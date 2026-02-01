import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.database.base import Base

if TYPE_CHECKING:
    from src.app.services.case import Case
    from src.app.services.company import Company


class ClientType(str, Enum):
    legal = "legal"
    individual = "individual"
    court = "court"


class ContactType(str, Enum):
    legal_representative = "legal_representative"
    court_officer = "court_officer"
    individual = "individual"


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # Уникальный идентификатор клиента
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, index=True)  # Полное наименование клиента
    short_name: Mapped[str | None] = mapped_column(Text)  # Сокращённое наименование клиента
    type: Mapped[ClientType] = mapped_column(SQLEnum(ClientType, native_enum=False), nullable=False)  # Тип клиента (физ/юр лицо/суд)
    inn: Mapped[str | None] = mapped_column(String(12), unique=True, index=True)  # ИНН клиента
    email: Mapped[str | None] = mapped_column(String(255))  # Email клиента
    phone: Mapped[str | None] = mapped_column(String(50))  # Телефон клиента
    legal_address: Mapped[str | None] = mapped_column(Text)  # Юридический адрес
    actual_address: Mapped[str | None] = mapped_column(Text)  # Фактический адрес
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())  # Дата создания
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())  # Дата обновления
    contacts: Mapped[list[Contact]] = relationship("Contact", back_populates="client", cascade="all, delete-orphan")  # Контакты клиента
    cases: Mapped[list[Case]] = relationship("Case", back_populates="client")  # Список дел клиента
    company: Mapped[Company] = relationship("Company", back_populates="clients")
    __table_args__ = (
        Index("ix_clients_name_prefix", "name", postgresql_ops={"name": "text_pattern_ops"}),
        Index("ix_clients_short_name_prefix", "short_name", postgresql_ops={"short_name": "text_pattern_ops"}),
    )


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # Уникальный идентификатор контакта
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )  # Ссылка на клиента с каскадным удалением
    name: Mapped[str] = mapped_column(Text, nullable=False)  # Имя контакта
    position: Mapped[str | None] = mapped_column(Text)  # Должность контакта
    email: Mapped[str | None] = mapped_column(String(255))  # Email контакта
    phone: Mapped[str | None] = mapped_column(String(50))  # Телефон контакта
    is_main: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # Признак основного контакта
    contact_type: Mapped[ContactType] = mapped_column(SQLEnum(ContactType, native_enum=False), nullable=False)  # Тип контакта
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())  # Дата создания
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())  # Дата обновления
    client: Mapped[Client] = relationship("Client", back_populates="contacts")  # Ссылка на клиента
