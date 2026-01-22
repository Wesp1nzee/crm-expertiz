import os
import uuid
from collections.abc import Sequence

from fastapi import UploadFile
from sqlalchemy import asc, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.storage.s3 import s3_storage
from src.app.services.document.models import Document


class DocumentService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def upload_document(
        self,
        file: UploadFile,
        case_id: uuid.UUID | None = None,
        title: str | None = None,
        user_id: uuid.UUID | None = None,
    ) -> Document:
        content = await file.read()
        file_ext = os.path.splitext(file.filename or "")[1].lower()

        folder = f"documents/{case_id}" if case_id else "general"
        s3_key = f"{folder}/{uuid.uuid4()}{file_ext}"

        await s3_storage.upload_file(
            file_data=content,
            object_key=s3_key,
            content_type=file.content_type or "application/octet-stream",
        )

        db_doc = Document(
            case_id=case_id,
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
        result = await self.db.execute(select(Document).where(Document.id == doc_id))
        doc = result.scalar_one_or_none()
        if not doc:
            return None
        return await s3_storage.get_download_url(doc.file_path)

    async def list_documents(
        self,
        case_id: uuid.UUID | None = None,
        search: str | None = None,
        sort_by: str = "created_at",
        order: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[Document]:
        query = select(Document)

        if case_id:
            query = query.where(Document.case_id == case_id)

        if search:
            query = query.where(Document.title.ilike(f"%{search}%"))

        column = getattr(Document, sort_by, Document.created_at)
        sort_func = desc if order == "desc" else asc
        query = query.order_by(sort_func(column))

        query = query.limit(limit).offset(offset)

        result = await self.db.execute(query)
        return result.scalars().all()

    async def delete_document(self, doc_id: uuid.UUID) -> bool:
        result = await self.db.execute(select(Document).where(Document.id == doc_id))
        doc = result.scalar_one_or_none()
        if not doc:
            return False

        await s3_storage.delete_file(doc.file_path)
        await self.db.delete(doc)
        await self.db.commit()
        return True
