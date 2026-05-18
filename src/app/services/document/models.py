import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import UUID, BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.database.base import TenantBase

if TYPE_CHECKING:
    from src.app.services.case.models import Case
    from src.app.services.share.models import DocumentShare
    from src.app.services.user.models import User


class ShareType(str, Enum):
    LINK = "link"  # Доступ по публичной ссылке
    USER = "user"  # Доступ конкретному сотруднику внутри CRM


class PermissionLevel(str, Enum):
    VIEW = "view"  # Только просмотр
    EDIT = "edit"  # Возможность переименовывать, перемещать или удалять


class ShareAccessType(str, Enum):
    """Типы действий для лога аудита"""

    VIEW = "view"  # Открытие ссылки/файла
    DOWNLOAD = "download"  # Скачивание файла
    PREVIEW = "preview"  # Просмотр превью в браузере


class Folder(TenantBase):
    __tablename__ = "folders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"), nullable=True, index=True)
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), nullable=True, index=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    parent: Mapped[Folder | None] = relationship("Folder", remote_side=[id], back_populates="subfolders")
    subfolders: Mapped[list[Folder]] = relationship("Folder", back_populates="parent", cascade="all, delete-orphan")
    documents: Mapped[list[Document]] = relationship("Document", back_populates="folder", cascade="all, delete-orphan")
    creator: Mapped[User | None] = relationship("User", back_populates="created_folders", foreign_keys=[created_by_id])
    case_root: Mapped[Case | None] = relationship(
        "Case",
        back_populates="root_folder",
        primaryjoin="Folder.id == Case.root_folder_id",
        foreign_keys="Case.root_folder_id",
        overlaps="root_folder",
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", index=True)
    shares: Mapped[list[DocumentShare]] = relationship("DocumentShare", back_populates="folder", cascade="all, delete-orphan")
    __table_args__ = (
        Index("ix_folders_company_id_case_id", "company_id", "case_id"),
        Index("ix_folders_company_id_parent_id", "company_id", "parent_id"),
        Index("ix_folders_company_id_created_at", "company_id", "created_at"),
        Index("ix_folders_company_id_name", "company_id", "name"),
        # Index("ix_documents_is_deleted", "is_deleted"),  ← УДАЛИТЬ
        Index("ix_folders_is_deleted", "is_deleted"),
    )


class Document(TenantBase):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), nullable=True, index=True)

    folder_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("folders.id", ondelete="SET NULL"), nullable=True, index=True)

    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    title: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    file_extension: Mapped[str] = mapped_column(String(10), nullable=False, index=True)

    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", index=True)
    uploaded_by: Mapped[User | None] = relationship("User", back_populates="uploaded_documents")
    folder: Mapped[Folder | None] = relationship("Folder", back_populates="documents")
    case: Mapped[Case | None] = relationship("Case", back_populates="documents")
    shares: Mapped[list[DocumentShare]] = relationship("DocumentShare", back_populates="document", cascade="all, delete-orphan")
    __table_args__ = (
        Index("ix_documents_company_id_case_id", "company_id", "case_id"),
        Index("ix_documents_company_id_folder_id", "company_id", "folder_id"),
        Index("ix_documents_company_id_created_at", "company_id", "created_at"),
        Index("ix_documents_company_id_is_archived", "company_id", "is_archived"),
        Index("ix_documents_company_id_mime_type", "company_id", "mime_type"),
    )
