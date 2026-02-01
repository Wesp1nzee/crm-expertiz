import asyncio
import os
import uuid

from fastapi import UploadFile
from sqlalchemy import asc, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.core.storage.s3 import s3_storage
from src.app.services.case.models import Case
from src.app.services.document.models import Document, Folder
from src.app.services.document.schemas import EntryType, FileSystemEntry, FolderCreate
from src.app.services.user.models import UserRole


class DocumentService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_folder(self, folder_data: FolderCreate, user_id: uuid.UUID, user_role: UserRole) -> Folder:
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
        user_id: uuid.UUID | None = None,
        user_role: UserRole | None = None,
    ) -> list[FileSystemEntry]:
        sort_func = desc if order == "desc" else asc

        folder_stmt = select(Folder).options(selectinload(Folder.creator))
        doc_stmt = select(Document).options(selectinload(Document.uploaded_by))

        if not search:
            folder_stmt = folder_stmt.where(Folder.parent_id == folder_id)
            doc_stmt = doc_stmt.where(Document.folder_id == folder_id)
        else:
            folder_stmt = folder_stmt.where(Folder.name.ilike(f"%{search}%"))
            doc_stmt = doc_stmt.where(or_(Document.title.ilike(f"%{search}%"), Document.original_filename.ilike(f"%{search}%")))

        if case_id:
            folder_stmt = folder_stmt.where(Folder.case_id == case_id)
            doc_stmt = doc_stmt.where(Document.case_id == case_id)

        if user_role == UserRole.EXPERT and not case_id:
            folder_stmt = folder_stmt.join(Case, Folder.case_id == Case.id).where(Case.assigned_user_id == user_id)
            doc_stmt = doc_stmt.join(Case, Document.case_id == Case.id).where(Case.assigned_user_id == user_id)

        f_sort_col = getattr(Folder, sort_by if hasattr(Folder, sort_by) else "created_at")
        d_sort_col = getattr(Document, sort_by if hasattr(Document, sort_by) else "created_at")

        folder_stmt = folder_stmt.order_by(sort_func(f_sort_col))
        doc_stmt = doc_stmt.order_by(sort_func(d_sort_col))

        f_res, d_res = await asyncio.gather(
            self.db.execute(folder_stmt.limit(limit).offset(offset)), self.db.execute(doc_stmt.limit(limit).offset(offset))
        )

        result: list[FileSystemEntry] = []

        for folder in f_res.scalars().all():
            result.append(
                FileSystemEntry(
                    id=folder.id,
                    name=folder.name,
                    type=EntryType.FOLDER,
                    created_at=folder.created_at,
                    created_by_id=folder.created_by_id,
                    created_by_name=folder.creator.full_name if folder.creator else None,
                    parent_id=folder.parent_id,
                )
            )

        for document in d_res.scalars().all():
            result.append(
                FileSystemEntry(
                    id=document.id,
                    name=document.title,
                    type=EntryType.FILE,
                    size=document.file_size,
                    extension=document.file_extension,
                    created_at=document.created_at,
                    created_by_id=document.uploaded_by_id,
                    created_by_name=document.uploaded_by.full_name if document.uploaded_by else None,
                    parent_id=document.folder_id,
                )
            )

        reverse = order == "desc"
        result.sort(key=lambda x: getattr(x, sort_by if hasattr(x, sort_by) else "created_at"), reverse=reverse)

        return result

    async def upload_document(
        self,
        file: UploadFile,
        user_id: uuid.UUID,
        case_id: uuid.UUID | None = None,
        folder_id: uuid.UUID | None = None,
        title: str | None = None,
    ) -> Document:
        content = await file.read()
        file_ext = os.path.splitext(file.filename or "")[1].lower()
        s3_key = f"documents/{uuid.uuid4()}{file_ext}"

        await s3_storage.upload_file(
            file_data=content,
            object_key=s3_key,
            content_type=file.content_type or "application/octet-stream",
        )

        final_title = title
        if title:
            if file_ext and not title.lower().endswith(file_ext.lower()):
                final_title = f"{title}{file_ext}"
        else:
            final_title = file.filename or "Untitled"

        db_doc = Document(
            case_id=case_id,
            folder_id=folder_id,
            title=final_title,
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

        if not doc:
            return None

        return await s3_storage.get_download_url(doc.file_path, original_filename=doc.original_filename)

    async def delete_document(self, doc_id: uuid.UUID) -> bool:
        res = await self.db.execute(select(Document).where(Document.id == doc_id))
        doc = res.scalar_one_or_none()
        if not doc:
            return False

        await s3_storage.delete_file(doc.file_path)
        await self.db.delete(doc)
        await self.db.commit()
        return True
