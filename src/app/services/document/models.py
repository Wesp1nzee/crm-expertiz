import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    UUID,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.app.core.database.base import Base

if TYPE_CHECKING:
    from src.app.services.case.models import Case
    from src.app.services.user.models import User


class Folder(Base):
    __tablename__ = "folders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Иерархия папок
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"), nullable=True, index=True)

    # Связь с делом (как в Document)
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), nullable=True, index=True)

    # Кто создал папку (для ролевой модели)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Рекурсивная связь для дерева папок
    parent: Mapped[Folder | None] = relationship("Folder", remote_side=[id], back_populates="subfolders")
    subfolders: Mapped[list[Folder]] = relationship("Folder", back_populates="parent", cascade="all, delete-orphan")

    # Связь с документами (удаляем документы при удалении папки)
    documents: Mapped[list[Document]] = relationship("Document", back_populates="folder", cascade="all, delete-orphan")

    # Связь с пользователем (создатель папки)
    creator: Mapped[User | None] = relationship("User")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Внешние ключи
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), nullable=True, index=True)
    folder_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("folders.id", ondelete="SET NULL"), nullable=True, index=True)
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    # Данные файла
    title: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_extension: Mapped[str] = mapped_column(String(10), nullable=False)

    # Состояние
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # Таймстампы
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    uploaded_by: Mapped[User | None] = relationship("User", back_populates="uploaded_documents")

    folder: Mapped[Folder | None] = relationship("Folder", back_populates="documents")

    case: Mapped[Case | None] = relationship("Case", back_populates="documents")
