import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.database.base import Base

if TYPE_CHECKING:
    from src.app.services.case.models import Case
    from src.app.services.user.models import User


class MailMessageStatus(str, Enum):
    DRAFT = "draft"
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    DELIVERED = "delivered"
    ERROR = "error"
    FAILED = "failed"


class MailMessageType(str, Enum):
    INCOMING = "incoming"
    OUTGOING = "outgoing"
    SYSTEM_NOTIFICATION = "system_notification"


class MailRecipientType(str, Enum):
    TO = "to"
    CC = "cc"
    BCC = "bcc"


class MailContent(Base):
    __tablename__ = "mail_contents"

    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mail_messages.id", ondelete="CASCADE"), primary_key=True)
    body_text: Mapped[str | None] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)

    message: Mapped[MailMessage] = relationship("MailMessage", back_populates="content")


class MailAttachment(Base):
    __tablename__ = "mail_attachments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mail_messages.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(100))
    file_size: Mapped[int] = mapped_column(BigInteger)
    stored_path: Mapped[str] = mapped_column(Text)
    attachment_id: Mapped[str] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    message: Mapped[MailMessage] = relationship("MailMessage", back_populates="attachments")


class MailRecipient(Base):
    __tablename__ = "mail_recipients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mail_messages.id", ondelete="CASCADE"))
    email_address: Mapped[str] = mapped_column(String(255), index=True)
    recipient_type: Mapped[MailRecipientType] = mapped_column(SQLEnum(MailRecipientType, native_enum=False))
    name: Mapped[str | None] = mapped_column(Text)

    message: Mapped[MailMessage] = relationship("MailMessage", back_populates="recipients")

    __table_args__ = (Index("ix_mail_recipients_email_message", "email_address", "message_id"),)


class MailMessage(Base):
    __tablename__ = "mail_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    external_message_id: Mapped[str | None] = mapped_column(String(500), index=True)
    thread_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("mail_messages.id", ondelete="SET NULL"))

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cases.id", ondelete="SET NULL"))

    sender_email: Mapped[str] = mapped_column(String(255))
    sender_name: Mapped[str | None] = mapped_column(String(255))
    subject: Mapped[str | None] = mapped_column(Text)

    message_type: Mapped[MailMessageType] = mapped_column(SQLEnum(MailMessageType, native_enum=False))
    status: Mapped[MailMessageStatus] = mapped_column(SQLEnum(MailMessageStatus, native_enum=False), default=MailMessageStatus.DELIVERED)

    is_read: Mapped[bool] = mapped_column(Boolean, server_default="false")
    is_important: Mapped[bool] = mapped_column(Boolean, server_default="false")
    is_starred: Mapped[bool] = mapped_column(Boolean, server_default="false")
    is_spam: Mapped[bool] = mapped_column(Boolean, server_default="false")
    is_archived: Mapped[bool] = mapped_column(Boolean, server_default="false")
    is_deleted: Mapped[bool] = mapped_column(Boolean, server_default="false")

    size_bytes: Mapped[int | None] = mapped_column(BigInteger)

    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    content: Mapped[MailContent] = relationship("MailContent", back_populates="message", uselist=False, cascade="all, delete-orphan")
    recipients: Mapped[list[MailRecipient]] = relationship("MailRecipient", back_populates="message", cascade="all, delete-orphan")
    attachments: Mapped[list[MailAttachment]] = relationship("MailAttachment", back_populates="message", cascade="all, delete-orphan")

    user: Mapped[User] = relationship("User", back_populates="mail_messages")
    case: Mapped[Case | None] = relationship("Case", back_populates="mail_messages")

    __table_args__ = (
        Index("ix_mail_messages_user_inbox", "user_id", "is_deleted", "is_spam", "processed_at"),
        Index("ix_mail_messages_user_unread", "user_id", "is_read", "is_deleted"),
        Index("ix_mail_messages_subject_search", text("to_tsvector('russian', subject)"), postgresql_using="gin"),
    )
