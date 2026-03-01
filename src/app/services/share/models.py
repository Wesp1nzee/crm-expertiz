import secrets
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import UUID, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Text, func
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.database.base import Base, TenantBase
from src.app.services.document import PermissionLevel, ShareAccessType, ShareType

if TYPE_CHECKING:
    from src.app.services.document import Document, Folder
    from src.app.services.user.models import User


class ShareBatch(TenantBase):
    """
    Представляет группу доступа. Хранит общие настройки шаринга:
    токен ссылки, пароль, срок действия и связь с получателем (если это внутренний доступ).
    """

    __tablename__ = "share_batches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    share_type: Mapped[ShareType] = mapped_column(SQLEnum(ShareType, native_enum=False), nullable=False)

    share_token: Mapped[str | None] = mapped_column(Text, unique=True, index=True, nullable=True, default=lambda: secrets.token_urlsafe(32))
    link_password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    shared_with_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )

    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    owner: Mapped[User] = relationship("User", foreign_keys=[owner_id])
    shared_with: Mapped[User] = relationship("User", foreign_keys=[shared_with_user_id])
    shares: Mapped[list[DocumentShare]] = relationship("DocumentShare", back_populates="batch", cascade="all, delete-orphan")
    access_logs: Mapped[list[ShareAccessLog]] = relationship("ShareAccessLog", back_populates="batch")


class DocumentShare(Base):
    """
    Связывает конкретный документ или папку с определенной группой доступа (ShareBatch).
    Определяет уровень разрешений для конкретного ресурса.
    """

    __tablename__ = "document_shares"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("share_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )

    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("folders.id", ondelete="CASCADE"), nullable=True, index=True
    )

    permission_level: Mapped[PermissionLevel] = mapped_column(SQLEnum(PermissionLevel, native_enum=False), default=PermissionLevel.VIEW)
    can_download: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    batch: Mapped[ShareBatch] = relationship("ShareBatch", back_populates="shares")
    document: Mapped[Document] = relationship("Document", back_populates="shares")
    folder: Mapped[Folder] = relationship("Folder", back_populates="shares")

    __table_args__ = (
        CheckConstraint(
            "(document_id IS NOT NULL AND folder_id IS NULL) OR (document_id IS NULL AND folder_id IS NOT NULL)",
            name="ck_document_shares_resource_presence",
        ),
        Index("ix_doc_share_resource_lookup", "document_id", "folder_id"),
    )


class ShareAccessLog(Base):
    """
    Логирование доступа
    """

    __tablename__ = "share_access_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("share_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )

    access_type: Mapped[ShareAccessType] = mapped_column(
        SQLEnum(ShareAccessType, native_enum=False), nullable=False, server_default="view", index=True
    )

    accessed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    accessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    batch: Mapped[ShareBatch] = relationship("ShareBatch", back_populates="access_logs")
