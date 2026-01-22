import asyncio
import os
import uuid

from fastapi import UploadFile
from sqlalchemy import asc, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.storage.s3 import s3_storage
from src.app.services.document.models import Document, Folder
from src.app.services.document.schemas import EntryType, FileSystemEntry, FolderCreate


class DocumentService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_folder(self, folder_data: FolderCreate, user_id: uuid.UUID | None) -> Folder:
        db_folder = Folder(**folder_data.model_dump(), created_by_id=user_id)
        self.db.add(db_folder)
        await self.db.commit()
        await self.db.refresh(db_folder)
        return db_folder

    async def get_unified_list(
        self,
        folder_id: uuid.UUID | None = None,
        case_id: uuid.UUID | None = None,
        search: str | None = None,
        sort_by: str = "created_at",
        order: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> list[FileSystemEntry]:
        sort_func = desc if order == "desc" else asc

        folder_stmt = select(Folder)
        doc_stmt = select(Document)

        if not search:
            folder_stmt = folder_stmt.where(Folder.parent_id == folder_id)
            doc_stmt = doc_stmt.where(Document.folder_id == folder_id)
        else:
            folder_stmt = folder_stmt.where(Folder.name.ilike(f"%{search}%"))
            doc_stmt = doc_stmt.where(Document.title.ilike(f"%{search}%"))

        if case_id:
            folder_stmt = folder_stmt.where(Folder.case_id == case_id)
            doc_stmt = doc_stmt.where(Document.case_id == case_id)

        f_sort_col = getattr(Folder, sort_by if hasattr(Folder, sort_by) else "created_at")
        d_sort_col = getattr(Document, sort_by if hasattr(Document, sort_by) else "created_at")

        folder_stmt = folder_stmt.order_by(sort_func(f_sort_col))
        doc_stmt = doc_stmt.order_by(sort_func(d_sort_col))

        f_res, d_res = await asyncio.gather(
            self.db.execute(folder_stmt.limit(limit).offset(offset)), self.db.execute(doc_stmt.limit(limit).offset(offset))
        )

        result: list[FileSystemEntry] = []
        for f in f_res.scalars().all():
            result.append(
                FileSystemEntry(
                    id=f.id, name=f.name, type=EntryType.FOLDER, created_at=f.created_at, created_by_id=f.created_by_id, parent_id=f.parent_id
                )
            )
        for d in d_res.scalars().all():
            result.append(
                FileSystemEntry(
                    id=d.id,
                    name=d.title,
                    type=EntryType.FILE,
                    size=d.file_size,
                    extension=d.file_extension,
                    created_at=d.created_at,
                    created_by_id=d.uploaded_by_id,
                    parent_id=d.folder_id,
                )
            )
        return result

    async def upload_document(
        self,
        file: UploadFile,
        case_id: uuid.UUID | None = None,
        folder_id: uuid.UUID | None = None,
        title: str | None = None,
        user_id: uuid.UUID | None = None,
    ) -> Document:
        content = await file.read()
        file_ext = os.path.splitext(file.filename or "")[1].lower()
        s3_key = f"documents/{uuid.uuid4()}{file_ext}"

        await s3_storage.upload_file(
            file_data=content,
            object_key=s3_key,
            content_type=file.content_type or "application/octet-stream",
        )

        db_doc = Document(
            case_id=case_id,
            folder_id=folder_id,
            title=title or file.filename or "Untitled",
            original_filename=file.filename or "unknown",
            file_path=s3_key,
            file_size=len(content),
            mime_type=file.content_type or "application/octet-stream",
            file_extension=file_ext,
            uploaded_by_id=user_id,
        )
        self.db.add(db_doc)
        await self.db.commit()
        await self.db.refresh(db_doc)
        return db_doc

    async def get_presigned_url(self, doc_id: uuid.UUID) -> str | None:
        res = await self.db.execute(select(Document).where(Document.id == doc_id))
        doc = res.scalar_one_or_none()
        return await s3_storage.get_download_url(doc.file_path) if doc else None

    async def delete_document(self, doc_id: uuid.UUID) -> bool:
        res = await self.db.execute(select(Document).where(Document.id == doc_id))
        doc = res.scalar_one_or_none()
        if not doc:
            return False
        await s3_storage.delete_file(doc.file_path)
        await self.db.delete(doc)
        await self.db.commit()
        return True
