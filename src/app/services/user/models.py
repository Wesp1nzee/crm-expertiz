import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, UUID, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.app.core.database.base import Base

if TYPE_CHECKING:
    from src.app.services.case import Case
    from src.app.services.company import Company
    from src.app.services.document import Document
    from src.app.services.mail import MailMessage


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    CEO = "ceo"
    ACCOUNTANT = "accountant"
    EXPERT = "expert"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[UserRole] = mapped_column(SQLEnum(UserRole, native_enum=False), nullable=False)
    # TODO: Добавить атрибут online
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    can_authenticate: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, server_default="{}")
    specialization: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    cases: Mapped[list[Case]] = relationship("Case", back_populates="assigned_user")
    email_config: Mapped[UserEmailConfig | None] = relationship(
        "UserEmailConfig", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    uploaded_documents: Mapped[list[Document]] = relationship("Document", back_populates="uploaded_by")
    mail_messages: Mapped[list[MailMessage]] = relationship("MailMessage", back_populates="user")
    company: Mapped[Company] = relationship("Company", back_populates="users")

    __table_args__ = (Index("ix_users_full_name_prefix", "full_name", postgresql_ops={"full_name": "text_pattern_ops"}),)


class UserEmailConfig(Base):
    __tablename__ = "user_email_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)

    smtp_host: Mapped[str] = mapped_column(Text)
    smtp_port: Mapped[int] = mapped_column(Integer)
    smtp_user: Mapped[str] = mapped_column(Text)
    smtp_password: Mapped[str] = mapped_column(Text)  # Encrypted

    imap_host: Mapped[str] = mapped_column(Text)
    imap_port: Mapped[int] = mapped_column(Integer)
    imap_user: Mapped[str] = mapped_column(Text)
    imap_password: Mapped[str] = mapped_column(Text)  # Encrypted

    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    user: Mapped[User] = relationship("User", back_populates="email_config")
