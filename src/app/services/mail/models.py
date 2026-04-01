import secrets
import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import UUID, BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.database.base import Base, TenantBase

if TYPE_CHECKING:
    from src.app.services.case.models import Case
    from src.app.services.user.models import User


def new_token() -> str:
    return secrets.token_urlsafe(32)


class EmailContactSource(str, Enum):
    CLIENT = "client"
    SENT = "sent"
    RECEIVED = "received"


class MailFolder(str, Enum):
    INBOX = "inbox"
    SENT = "sent"
    DRAFTS = "drafts"
    SPAM = "spam"
    TRASH = "trash"


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


class MailAttachment(TenantBase):
    """
    Единое хранилище метаданных всех вложений в S3.
    Поддерживает как обычные MIME-вложения, так и файлы по ссылке (oversized).
    """

    __tablename__ = "mail_attachments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    mail_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mail_messages.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mail_oversized_batches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    s3_key: Mapped[str] = mapped_column(Text, nullable=False)
    s3_bucket: Mapped[str] = mapped_column(String(255), nullable=False)

    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False, default="application/octet-stream")
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)

    attachment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    message: Mapped[MailMessage | None] = relationship("MailMessage", back_populates="attachments")
    batch: Mapped[MailOversizedBatch | None] = relationship("MailOversizedBatch", back_populates="files")

    __table_args__ = (Index("ix_mail_attachments_company_message", "company_id", "mail_message_id"),)


class MailOversizedBatch(TenantBase):
    """
    Группа файлов, вынесенных из письма из-за превышения лимита.
    Токен share_token используется в публичной ссылке.
    """

    __tablename__ = "mail_oversized_batches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    mail_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mail_messages.id", ondelete="CASCADE"),
        index=True,
    )

    share_token: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        default=new_token,
        index=True,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relations
    files: Mapped[list[MailAttachment]] = relationship(
        "MailAttachment",
        back_populates="batch",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    message: Mapped[MailMessage] = relationship("MailMessage", back_populates="oversized_batches")


class MailContent(Base):
    __tablename__ = "mail_contents"

    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mail_messages.id", ondelete="CASCADE"),
        primary_key=True,
    )
    body_text: Mapped[str | None] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)

    message: Mapped[MailMessage] = relationship("MailMessage", back_populates="content")


class MailRecipient(Base):
    __tablename__ = "mail_recipients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mail_messages.id", ondelete="CASCADE"))
    email_address: Mapped[str] = mapped_column(String(255), index=True)
    recipient_type: Mapped[MailRecipientType] = mapped_column(SQLEnum(MailRecipientType, native_enum=False))
    name: Mapped[str | None] = mapped_column(Text)

    message: Mapped[MailMessage] = relationship("MailMessage", back_populates="recipients")

    __table_args__ = (Index("ix_mail_recipients_email_message", "email_address", "message_id"),)


class MailMessage(TenantBase):
    __tablename__ = "mail_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    external_message_id: Mapped[str | None] = mapped_column(String(500), index=True)
    thread_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("mail_messages.id", ondelete="SET NULL"))

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cases.id", ondelete="SET NULL"), nullable=True)

    sender_email: Mapped[str] = mapped_column(String(255))
    sender_name: Mapped[str | None] = mapped_column(String(255))
    reply_to: Mapped[str | None] = mapped_column(String(255))
    subject: Mapped[str | None] = mapped_column(Text)

    is_starred: Mapped[bool] = mapped_column(Boolean, server_default="false", default=False)
    is_important: Mapped[bool] = mapped_column(Boolean, server_default="false", default=False)
    is_spam: Mapped[bool] = mapped_column(Boolean, server_default="false", default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, server_default="false", default=False)

    folder: Mapped[MailFolder] = mapped_column(
        SQLEnum(MailFolder, native_enum=False),
        default=MailFolder.INBOX,
        server_default="inbox",
        index=True,
    )
    message_type: Mapped[MailMessageType] = mapped_column(SQLEnum(MailMessageType, native_enum=False))
    status: Mapped[MailMessageStatus] = mapped_column(
        SQLEnum(MailMessageStatus, native_enum=False),
        default=MailMessageStatus.DELIVERED,
    )

    is_read: Mapped[bool] = mapped_column(Boolean, server_default="false")
    is_deleted: Mapped[bool] = mapped_column(Boolean, server_default="false")

    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    imap_uid: Mapped[int | None] = mapped_column(BigInteger, index=True)

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relations
    content: Mapped[MailContent] = relationship("MailContent", back_populates="message", uselist=False, cascade="all, delete-orphan")
    recipients: Mapped[list[MailRecipient]] = relationship("MailRecipient", back_populates="message", cascade="all, delete-orphan")
    attachments: Mapped[list[MailAttachment]] = relationship("MailAttachment", back_populates="message", cascade="all, delete-orphan")
    oversized_batches: Mapped[list[MailOversizedBatch]] = relationship(
        "MailOversizedBatch", back_populates="message", cascade="all, delete-orphan"
    )

    user: Mapped[User] = relationship("User", back_populates="mail_messages")
    case: Mapped[Case | None] = relationship("Case", back_populates="mail_messages")

    __table_args__ = (
        Index("ix_mail_messages_company_folder", "company_id", "folder", "is_deleted", "sent_at"),
        Index("ix_mail_messages_company_unread", "company_id", "is_read", "is_deleted"),
    )


class MailSyncState(TenantBase):
    """Состояние синхронизации IMAP."""

    __tablename__ = "mail_sync_states"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    folder: Mapped[str] = mapped_column(String(50), default="inbox", server_default="inbox", index=True)
    last_synced_uid: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("ix_mail_sync_states_unique", "company_id", "user_id", "folder", unique=True),)


class EmailContact(TenantBase):
    __tablename__ = "email_contacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    source: Mapped[EmailContactSource] = mapped_column(
        SQLEnum(EmailContactSource, native_enum=False),
        nullable=False,
    )

    usage_count: Mapped[int] = mapped_column(Integer, server_default="0", default=0, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("company_id", "email", name="uq_email_contacts_company_email"),
        Index(
            "ix_email_contacts_email_prefix",
            "company_id",
            "email",
            postgresql_ops={"email": "text_pattern_ops"},
        ),
        Index(
            "ix_email_contacts_name_prefix",
            "company_id",
            "name",
            postgresql_ops={"name": "text_pattern_ops"},
        ),
        Index(
            "ix_email_contacts_usage",
            "company_id",
            "usage_count",
            "last_used_at",
        ),
    )
