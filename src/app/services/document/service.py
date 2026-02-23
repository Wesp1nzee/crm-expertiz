import os
import uuid
import zipfile
from datetime import datetime
from typing import Any

from fastapi import HTTPException, UploadFile, status
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

    async def create_folder(self, folder_data: FolderCreate, user_id: uuid.UUID, company_id: uuid.UUID) -> Folder:
        db_folder = Folder(**folder_data.model_dump(), created_by_id=user_id, company_id=company_id)
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
        company_id: uuid.UUID | None = None,
    ) -> list[FileSystemEntry]:
        folder_stmt = select(Folder).options(selectinload(Folder.creator)).where(Folder.company_id == company_id)
        doc_stmt = select(Document).options(selectinload(Document.uploaded_by)).where(Document.company_id == company_id)

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

        sort_func = desc if order == "desc" else asc
        f_sort_col = getattr(Folder, sort_by if hasattr(Folder, sort_by) else "created_at")
        d_sort_col = getattr(Document, sort_by if hasattr(Document, sort_by) else "created_at")

        folder_stmt = folder_stmt.order_by(sort_func(f_sort_col)).limit(limit).offset(offset)
        doc_stmt = doc_stmt.order_by(sort_func(d_sort_col)).limit(limit).offset(offset)

        folders_result = await self.db.execute(folder_stmt)
        documents_result = await self.db.execute(doc_stmt)

        folders = folders_result.scalars().all()
        documents = documents_result.scalars().all()

        result: list[FileSystemEntry] = []
        for folder in folders:
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
        for document in documents:
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

        def sort_key(x: FileSystemEntry) -> str | int | datetime:
            attr_name = sort_by if hasattr(x, sort_by) else "created_at"
            val = getattr(x, attr_name)
            return 0 if val is None and sort_by == "size" else (val or "")

        result.sort(key=sort_key, reverse=reverse)
        return result

    async def upload_document(
        self,
        file: UploadFile,
        user_id: uuid.UUID,
        company_id: uuid.UUID,
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

        final_title = title if title else file.filename or "Untitled"
        if title and file_ext and not title.lower().endswith(file_ext.lower()):
            final_title = f"{title}{file_ext}"

        target_company_id: uuid.UUID = company_id
        effective_folder_id: uuid.UUID | None = folder_id

        if case_id:
            result = await self.db.execute(select(Case).where(Case.id == case_id))
            case = result.scalar_one_or_none()

            if not case:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Указанное дело не найдено")

            if case.company_id:
                target_company_id = case.company_id

            if effective_folder_id is None:
                effective_folder_id = case.root_folder_id

        db_doc = Document(
            case_id=case_id,
            folder_id=effective_folder_id,
            title=final_title,
            original_filename=file.filename or "unknown",
            file_path=s3_key,
            file_size=len(content),
            mime_type=file.content_type or "application/octet-stream",
            file_extension=file_ext,
            uploaded_by_id=user_id,
            company_id=target_company_id,
        )

        self.db.add(db_doc)
        await self.db.commit()
        await self.db.refresh(db_doc)
        return db_doc

    async def get_presigned_url(
        self,
        doc_id: uuid.UUID,
        company_id: uuid.UUID,
        download: bool = False,
    ) -> str | None:
        res = await self.db.execute(select(Document).where(Document.id == doc_id, Document.company_id == company_id))
        doc = res.scalar_one_or_none()
        if not doc:
            return None
        return await s3_storage.get_presigned_url(
            object_key=doc.file_path,
            original_filename=doc.original_filename,
            download=download,
        )

    async def delete_document(self, doc_id: uuid.UUID, company_id: uuid.UUID) -> bool:
        res = await self.db.execute(select(Document).where(Document.id == doc_id, Document.company_id == company_id))
        doc = res.scalar_one_or_none()
        if not doc:
            return False
        await s3_storage.delete_file(doc.file_path)
        await self.db.delete(doc)
        await self.db.commit()
        return True

    async def add_folder_to_zip(
        self,
        zip_file: zipfile.ZipFile,
        folder_id: uuid.UUID,
        path_prefix: str,
        user_id: uuid.UUID,
        user_role: UserRole,
        company_id: uuid.UUID,
    ) -> None:
        folders_query = select(Folder).where(Folder.parent_id == folder_id, Folder.company_id == company_id)
        documents_query = select(Document).where(Document.folder_id == folder_id, Document.company_id == company_id)

        folders_result = await self.db.execute(folders_query)
        documents_result = await self.db.execute(documents_query)
        folders = folders_result.scalars().all()
        documents = documents_result.scalars().all()

        for doc in documents:
            if await self._check_document_access(doc, user_id, user_role, company_id):
                file_content = await s3_storage.get_file_content(doc.file_path)
                zip_path = f"{path_prefix}{doc.title}" if path_prefix else doc.title
                zip_file.writestr(zip_path, file_content)

        for folder in folders:
            if await self._check_folder_access(folder, user_id, user_role, company_id):
                subfolder_path = f"{path_prefix}{folder.name}/" if path_prefix else f"{folder.name}/"
                zip_file.writestr(subfolder_path, "")
                await self.add_folder_to_zip(
                    zip_file,
                    folder.id,
                    subfolder_path,
                    user_id,
                    user_role,
                    company_id,
                )

    async def update_document(
        self,
        document_id: uuid.UUID,
        update_data: dict[str, Any],
        user_id: uuid.UUID,
        user_role: UserRole,
        company_id: uuid.UUID,
    ) -> Document | None:
        res = await self.db.execute(
            select(Document)
            .where(Document.id == document_id, Document.company_id == company_id)
            .options(selectinload(Document.folder), selectinload(Document.case))
        )
        doc = res.scalar_one_or_none()
        if not doc:
            return None

        if not await self._check_document_access(doc, user_id, user_role, company_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет прав для обновления этого документа")

        if "title" in update_data and update_data["title"]:
            doc.title = update_data["title"]

        if "case_id" in update_data:
            new_case_id = update_data["case_id"]
            if new_case_id:
                new_case_res = await self.db.execute(select(Case).where(Case.id == new_case_id, Case.company_id == company_id))
                if not new_case_res.scalar_one_or_none():
                    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нельзя переместить документ в кейс другой компании")
                doc.case_id = new_case_id

        if "folder_id" in update_data:
            new_folder_id = update_data["folder_id"]

            if new_folder_id is not None:
                folder_res = await self.db.execute(select(Folder).where(Folder.id == new_folder_id, Folder.company_id == company_id))
                folder = folder_res.scalar_one_or_none()
                if not folder:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Целевая папка не найдена")
                if await self._is_descendant_folder(doc.id, new_folder_id):
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Нельзя переместить документ в дочернюю папку")

            doc.folder_id = new_folder_id

        doc.updated_at = datetime.now()
        await self.db.commit()
        await self.db.refresh(doc)
        return doc

    async def update_folder(
        self,
        folder_id: uuid.UUID,
        update_data: dict[str, Any],
        user_id: uuid.UUID,
        user_role: UserRole,
        company_id: uuid.UUID,
    ) -> Folder | None:
        res = await self.db.execute(
            select(Folder)
            .where(Folder.id == folder_id, Folder.company_id == company_id)
            .options(selectinload(Folder.parent), selectinload(Folder.subfolders))
        )
        folder = res.scalar_one_or_none()
        if not folder:
            return None

        if not await self._check_folder_access(folder, user_id, user_role, company_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет прав для обновления этой папки")

        if "name" in update_data and update_data["name"]:
            folder.name = update_data["name"]

        if "case_id" in update_data:
            new_case_id = update_data["case_id"]
            if new_case_id:
                new_case_res = await self.db.execute(select(Case).where(Case.id == new_case_id, Case.company_id == company_id))
                if not new_case_res.scalar_one_or_none():
                    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нельзя переместить папку в кейс другой компании")
                folder.case_id = new_case_id

        if "parent_id" in update_data:
            new_parent_id = update_data["parent_id"]
            if new_parent_id == folder_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Нельзя переместить папку в саму себя")
            if new_parent_id and await self._is_descendant_folder(new_parent_id, folder_id):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Нельзя переместить папку в дочернюю папку")
            if new_parent_id:
                parent_res = await self.db.execute(select(Folder).where(Folder.id == new_parent_id, Folder.company_id == company_id))
                if not parent_res.scalar_one_or_none():
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Родительская папка не найдена")
            folder.parent_id = new_parent_id

        folder.updated_at = datetime.now()
        await self.db.commit()
        await self.db.refresh(folder)
        return folder

    async def _is_descendant_folder(self, potential_parent_id: uuid.UUID, folder_id: uuid.UUID) -> bool:
        """
        Проверяет, является ли папка с folder_id дочерней (или вложенной) для potential_parent_id
        """
        current_id = folder_id
        visited = set()
        while current_id:
            if current_id in visited:
                break
            visited.add(current_id)
            res = await self.db.execute(select(Folder.parent_id).where(Folder.id == current_id))
            parent = res.scalar_one_or_none()
            if not parent:
                break
            if parent == potential_parent_id:
                return True
            current_id = parent
        return False

    async def _check_document_access(
        self,
        document: Document,
        user_id: uuid.UUID,
        user_role: UserRole,
        company_id: uuid.UUID,
    ) -> bool:
        """
        Проверяет права доступа к документу
        """
        if document.company_id != company_id:
            return False

        if user_role in [UserRole.ADMIN, UserRole.CEO, UserRole.ACCOUNTANT]:
            return True

        if document.uploaded_by_id == user_id:
            return True

        return False

    async def _check_folder_access(
        self,
        folder: Folder,
        user_id: uuid.UUID,
        user_role: UserRole,
        company_id: uuid.UUID,
    ) -> bool:
        """
        Проверяет права доступа к папке
        """
        if folder.company_id != company_id:
            return False

        if user_role in [UserRole.ADMIN, UserRole.CEO, UserRole.ACCOUNTANT]:
            return True

        if folder.created_by_id == user_id:
            return True

        return False
