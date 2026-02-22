import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import UUID, BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.database.base import TenantBase

if TYPE_CHECKING:
    from src.app.services.case.models import Case
    from src.app.services.user.models import User


class Folder(TenantBase):
    __tablename__ = "folders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # Иерархия папок
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"), nullable=True, index=True)

    # Связь с делом
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), nullable=True, index=True)

    # Кто создал папку
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Рекурсивная связь для дерева папок
    parent: Mapped[Folder | None] = relationship("Folder", remote_side=[id], back_populates="subfolders")
    subfolders: Mapped[list[Folder]] = relationship("Folder", back_populates="parent", cascade="all, delete-orphan")

    # Связь с документами (удаляем документы при удалении папки)
    documents: Mapped[list[Document]] = relationship("Document", back_populates="folder", cascade="all, delete-orphan")

    # Связь с пользователем (создатель папки)
    creator: Mapped[User | None] = relationship("User")

    __table_args__ = (
        Index("ix_folders_company_id_case_id", "company_id", "case_id"),  # Поиск папок по делу в рамках компании
        Index("ix_folders_company_id_parent_id", "company_id", "parent_id"),  # Построение дерева папок
        Index("ix_folders_company_id_created_at", "company_id", "created_at"),  # Сортировка по дате создания
        Index("ix_folders_company_id_name", "company_id", "name"),  #  Поиск по имени в рамках компании
    )


class Document(TenantBase):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), nullable=True, index=True)
    folder_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("folders.id", ondelete="SET NULL"), nullable=True, index=True)
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    # Данные файла
    title: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    file_extension: Mapped[str] = mapped_column(String(10), nullable=False, index=True)

    # Состояние
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", index=True)

    # Таймстампы
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    uploaded_by: Mapped[User | None] = relationship("User", back_populates="uploaded_documents")
    folder: Mapped[Folder | None] = relationship("Folder", back_populates="documents")
    case: Mapped[Case | None] = relationship("Case", back_populates="documents")

    __table_args__ = (
        Index("ix_documents_company_id_case_id", "company_id", "case_id"),  # Документы по делу в рамках компании
        Index("ix_documents_company_id_folder_id", "company_id", "folder_id"),  # Документы по папке в рамках компании
        Index("ix_documents_company_id_created_at", "company_id", "created_at"),  # Хронология документов
        Index("ix_documents_company_id_is_archived", "company_id", "is_archived"),  # Фильтр архивных в рамках компании
        Index("ix_documents_company_id_mime_type", "company_id", "mime_type"),  # Поиск по типу файла
    )
