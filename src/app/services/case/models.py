import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import UUID, Column, DateTime, ForeignKey, Index, Numeric, String, Table, Text, func
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.database.base import Base, TenantBase

if TYPE_CHECKING:
    from src.app.services.client import Client
    from src.app.services.document import Document, Folder
    from src.app.services.mail import MailMessage
    from src.app.services.user import User

case_experts = Table(
    "case_experts",
    Base.metadata,
    Column(
        "case_id",
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "user_id",
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "assigned_at",
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    ),
)


class CaseStatus(str, Enum):
    archive = "archive"
    in_work = "in_work"
    debt = "debt"
    executed = "executed"
    withdrawn = "withdrawn"
    cancelled = "cancelled"
    fssp = "fssp"


class LegalEntityType(str, Enum):
    OOO = "ООО"
    IP = "ИП"


class Case(TenantBase):
    __tablename__ = "cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    number: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    case_number: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    authority: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    case_type: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    object_type: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    object_address: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    status: Mapped[CaseStatus] = mapped_column(
        SQLEnum(CaseStatus, native_enum=False),
        nullable=False,
        default=CaseStatus.in_work,
        index=True,
    )

    legal_entity_type: Mapped[LegalEntityType] = mapped_column(
        SQLEnum(LegalEntityType, native_enum=False), nullable=False, default=LegalEntityType.OOO, index=True
    )

    additional_materials_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    execution_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    completion_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    cost: Mapped[Decimal] = mapped_column(Numeric(precision=12, scale=2, asdecimal=True), nullable=False)
    bank_transfer_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), server_default="0.00", default=Decimal("0.00"))
    cash_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), server_default="0.00", default=Decimal("0.00"))
    remaining_debt: Mapped[Decimal] = mapped_column(Numeric(12, 2), server_default="0.00", default=Decimal("0.00"))
    debit: Mapped[Decimal] = mapped_column(Numeric(12, 2), server_default="0.00", default=Decimal("0.00"), index=True)
    plaintiff: Mapped[str | None] = mapped_column(Text, index=True)
    defendant: Mapped[str | None] = mapped_column(Text, index=True)
    judge_name: Mapped[str | None] = mapped_column(Text, index=True)
    remarks: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    registration_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    client: Mapped[Client] = relationship("Client", back_populates="cases")
    experts: Mapped[list[User]] = relationship(
        "User",
        secondary="case_experts",
        back_populates="expert_cases",
        lazy="selectin",
    )

    documents: Mapped[list[Document]] = relationship("Document", back_populates="case", cascade="all, delete-orphan")
    mail_messages: Mapped[list[MailMessage]] = relationship("MailMessage", back_populates="case")

    root_folder_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("folders.id", ondelete="SET NULL", name="fk_case_root_folder_id_folders", use_alter=True),
        nullable=True,
        index=True,
        unique=True,
    )
    root_folder: Mapped[Folder | None] = relationship(
        "Folder",
        back_populates="case_root",
        primaryjoin="Case.root_folder_id == Folder.id",
        foreign_keys=[root_folder_id],
    )

    __table_args__ = (
        Index("ix_cases_company_id_client_id", "company_id", "client_id"),
        Index("ix_cases_company_id_status", "company_id", "status"),
        Index("ix_cases_company_id_deadline", "company_id", "deadline"),
        Index("ix_cases_company_id_created_at", "company_id", "created_at"),
        Index("ix_cases_number_btree", "number"),
        Index("ix_cases_case_number_btree", "case_number"),
    )
