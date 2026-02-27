import asyncio
import logging
import os
import uuid
import zipfile
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import CTE, Text, cast, delete, literal_column, or_, select, text, union_all, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import Select

from src.app.core.storage.s3 import s3_storage
from src.app.services.case.models import Case
from src.app.services.document.models import Document, Folder
from src.app.services.document.schemas import FileSystemEntry, FolderCreate
from src.app.services.user.models import User, UserRole

logger = logging.getLogger(__name__)


class DocumentService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_folder(
        self,
        folder_data: FolderCreate,
        user_id: uuid.UUID,
        company_id: uuid.UUID,
    ) -> Folder:
        effective_parent_id = folder_data.parent_id

        if folder_data.parent_id is not None and not folder_data.case_id:
            parent_check = await self.db.execute(
                select(Folder.id).where(
                    Folder.id == folder_data.parent_id,
                    Folder.company_id == company_id,
                )
            )
            if not parent_check.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Родительская папка не найдена",
                )

        if folder_data.case_id:
            case_result = await self.db.execute(select(Case).where(Case.id == folder_data.case_id, Case.company_id == company_id))
            case = case_result.scalar_one_or_none()
            if not case:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Указанное дело не найдено")

            if folder_data.parent_id is not None:
                parent_check = await self.db.execute(
                    select(Folder.case_id).where(
                        Folder.id == folder_data.parent_id,
                        Folder.company_id == company_id,
                    )
                )
                parent_case_id = parent_check.scalar_one_or_none()
                effective_parent_id = folder_data.parent_id if parent_case_id == folder_data.case_id else case.root_folder_id
            else:
                effective_parent_id = case.root_folder_id

        db_folder = Folder(
            **folder_data.model_dump(exclude={"parent_id"}),
            parent_id=effective_parent_id,
            created_by_id=user_id,
            company_id=company_id,
        )

        self.db.add(db_folder)
        await self.db.commit()
        await self.db.refresh(db_folder)
        return db_folder

    async def update_folder(
        self,
        folder_id: uuid.UUID,
        update_data: dict[str, Any],
        user_id: uuid.UUID,
        user_role: UserRole,
        company_id: uuid.UUID,
    ) -> Folder | None:
        update_data = dict(update_data)

        res = await self.db.execute(
            select(Folder)
            .where(Folder.id == folder_id, Folder.company_id == company_id)
            .options(selectinload(Folder.parent), selectinload(Folder.subfolders))
        )
        folder = res.scalar_one_or_none()
        if not folder:
            return None

        if not await self._check_folder_access(folder, user_id, user_role, company_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Нет прав для обновления этой папки",
            )

        if "name" in update_data and update_data["name"]:
            folder.name = update_data["name"]

        if "parent_id" in update_data:
            new_parent_id = update_data["parent_id"]

            if new_parent_id is not None:
                if new_parent_id == folder_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Нельзя переместить папку в саму себя",
                    )

                if await self._is_descendant_folder(new_parent_id, folder_id):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Нельзя переместить папку в дочернюю папку",
                    )

                parent_res = await self.db.execute(select(Folder).where(Folder.id == new_parent_id, Folder.company_id == company_id))
                target_parent = parent_res.scalar_one_or_none()
                if not target_parent:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Родительская папка не найдена",
                    )

                if "case_id" not in update_data:
                    update_data["case_id"] = target_parent.case_id

                folder.parent_id = new_parent_id

        if "case_id" in update_data:
            new_case_id = update_data["case_id"]

            current_parent_id = folder.parent_id
            if new_case_id and current_parent_id:
                parent_check = await self.db.execute(
                    select(Folder.case_id).where(
                        Folder.id == current_parent_id,
                        Folder.company_id == company_id,
                    )
                )
                parent_case_id = parent_check.scalar_one_or_none()
                if parent_case_id != new_case_id:
                    update_data.pop("parent_id", None)

            folder.case_id = new_case_id

            await self._update_subtree_case_id(folder_id, new_case_id, company_id)

        folder.updated_at = datetime.now(UTC)
        await self.db.commit()
        await self.db.refresh(folder)
        return folder

    async def delete_folder(self, folder_id: uuid.UUID, company_id: uuid.UUID) -> None:
        res = await self.db.execute(select(Folder).where(Folder.id == folder_id, Folder.company_id == company_id))
        folder = res.scalar_one_or_none()
        if not folder:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Папка не найдена")

        file_paths = await self._collect_file_paths_in_tree([folder_id], company_id)

        await self.db.delete(folder)
        await self.db.commit()

        await self._delete_s3_files_safe(file_paths)

    async def upload_document(
        self,
        file: UploadFile,
        user_id: uuid.UUID,
        company_id: uuid.UUID,
        case_id: uuid.UUID | None = None,
        folder_id: uuid.UUID | None = None,
        title: str | None = None,
    ) -> Document:
        file_ext = os.path.splitext(file.filename or "")[1].lower()
        s3_key = f"documents/{uuid.uuid4()}{file_ext}"
        final_title = title if title else file.filename or "Untitled"

        effective_folder_id = folder_id
        if case_id:
            case_res = await self.db.execute(select(Case).where(Case.id == case_id, Case.company_id == company_id))
            case = case_res.scalar_one_or_none()
            if not case:
                raise HTTPException(status_code=404, detail="Дело не найдено")

            if folder_id:
                f_check = await self.db.execute(select(Folder.case_id).where(Folder.id == folder_id, Folder.company_id == company_id))
                if f_check.scalar_one_or_none() != case_id:
                    effective_folder_id = case.root_folder_id
            else:
                effective_folder_id = case.root_folder_id

        db_doc = Document(
            case_id=case_id,
            folder_id=effective_folder_id,
            title=final_title,
            original_filename=file.filename or "unknown",
            file_path=s3_key,
            file_size=file.size,
            mime_type=file.content_type or "application/octet-stream",
            file_extension=file_ext,
            uploaded_by_id=user_id,
            company_id=company_id,
        )

        self.db.add(db_doc)
        await self.db.flush()

        try:
            await s3_storage.upload_file(
                file_obj=file.file,
                object_key=s3_key,
                content_type=file.content_type or "application/octet-stream",
            )
            await self.db.commit()
        except Exception as err:
            await self.db.rollback()
            logger.exception("Ошибка при загрузке в S3: %s", s3_key)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Ошибка хранилища. Запись не создана.") from err

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

        file_path = doc.file_path

        await self.db.delete(doc)
        await self.db.commit()

        await self._delete_s3_files_safe([file_path])
        return True

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
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Нет прав для обновления этого документа",
            )

        if "title" in update_data and update_data["title"]:
            doc.title = update_data["title"]

        if "folder_id" in update_data:
            new_folder_id = update_data["folder_id"]

            if new_folder_id is not None:
                folder_res = await self.db.execute(select(Folder).where(Folder.id == new_folder_id, Folder.company_id == company_id))
                target_folder = folder_res.scalar_one_or_none()
                if not target_folder:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Целевая папка не найдена",
                    )

                if doc.folder_id and await self._is_descendant_folder(new_folder_id, doc.folder_id):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Нельзя переместить документ в дочернюю папку",
                    )

                if "case_id" not in update_data:
                    doc.case_id = target_folder.case_id

                doc.folder_id = new_folder_id

        if "case_id" in update_data:
            new_case_id = update_data["case_id"]
            current_folder_id = doc.folder_id

            if new_case_id and current_folder_id:
                folder_check = await self.db.execute(
                    select(Folder.case_id).where(
                        Folder.id == current_folder_id,
                        Folder.company_id == company_id,
                    )
                )
                folder_case_id = folder_check.scalar_one_or_none()
                if folder_case_id != new_case_id:
                    case_res = await self.db.execute(select(Case.root_folder_id).where(Case.id == new_case_id, Case.company_id == company_id))
                    doc.folder_id = case_res.scalar_one_or_none()

            doc.case_id = new_case_id

        doc.updated_at = datetime.now(UTC)
        await self.db.commit()
        await self.db.refresh(doc)
        return doc

    async def delete_bulk(
        self,
        folder_ids: list[uuid.UUID] | None,
        document_ids: list[uuid.UUID] | None,
        company_id: uuid.UUID,
    ) -> None:
        """Массовое удаление папок со всем содержимым и отдельных документов."""

        valid_root_ids: list[uuid.UUID] = []
        folder_doc_ids: set[uuid.UUID] = set()
        all_file_paths: list[str] = []

        if folder_ids:
            roots_result = await self.db.execute(
                select(Folder.id).where(
                    Folder.id.in_(folder_ids),
                    Folder.company_id == company_id,
                )
            )
            valid_root_ids = [row[0] for row in roots_result.all()]

            if len(valid_root_ids) != len(set(folder_ids)):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Не все папки доступны или существуют",
                )

            folder_file_paths, folder_doc_ids = await self._collect_file_paths_and_ids_in_tree(valid_root_ids, company_id)
            all_file_paths.extend(folder_file_paths)

        if document_ids:
            loose_docs_result = await self.db.execute(
                select(Document.id, Document.file_path).where(
                    Document.id.in_(document_ids),
                    Document.company_id == company_id,
                )
            )
            loose_docs = loose_docs_result.all()

            if len(loose_docs) != len(set(document_ids)):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Не все документы доступны или существуют",
                )

            for doc_id, file_path in loose_docs:
                if doc_id not in folder_doc_ids:
                    all_file_paths.append(file_path)

        loose_only_ids = [doc_id for doc_id, _ in (loose_docs if document_ids else []) if doc_id not in folder_doc_ids]

        if loose_only_ids:
            await self.db.execute(
                delete(Document).where(
                    Document.id.in_(loose_only_ids),
                    Document.company_id == company_id,
                )
            )

        if valid_root_ids:
            await self.db.execute(
                delete(Folder).where(
                    Folder.id.in_(valid_root_ids),
                    Folder.company_id == company_id,
                )
            )

        await self.db.commit()

        await self._delete_s3_files_safe(all_file_paths)

    async def add_folder_to_zip(
        self,
        zip_file: zipfile.ZipFile,
        folder_id: uuid.UUID,
        path_prefix: str,
        user_id: uuid.UUID,
        user_role: UserRole,
        company_id: uuid.UUID,
    ) -> None:
        """
        Рекурсивно собирает содержимое папки и добавляет в ZIP с сохранением структуры.
        Использует потоковую передачу данных из S3
        """

        cte_base_query = select(Folder.id, Folder.name, Folder.parent_id, cast(Folder.name, Text).label("full_path")).where(
            Folder.id == folder_id, Folder.company_id == company_id
        )

        if user_role == UserRole.EXPERT:
            cte_base_query = cte_base_query.where(Folder.created_by_id == user_id)

        folder_hierarchy_cte: CTE = cte_base_query.cte(name="folder_hierarchy", recursive=True)

        recursive_part = select(
            Folder.id, Folder.name, Folder.parent_id, (folder_hierarchy_cte.c.full_path + "/" + Folder.name).label("full_path")
        ).join(folder_hierarchy_cte, Folder.parent_id == folder_hierarchy_cte.c.id)

        folder_tree_union = folder_hierarchy_cte.union_all(recursive_part)
        folder_tree_subquery: CTE = folder_tree_union.alias("folder_tree")

        docs_stmt = (
            select(Document, folder_tree_subquery.c.full_path)
            .join(folder_tree_subquery, Document.folder_id == folder_tree_subquery.c.id)
            .where(Document.company_id == company_id)
        )

        if user_role == UserRole.EXPERT:
            docs_stmt = docs_stmt.where(Document.uploaded_by_id == user_id)

        result = await self.db.execute(docs_stmt)
        rows = result.all()

        semaphore = asyncio.Semaphore(5)
        zip_lock = asyncio.Lock()

        async def process_document(doc: Document, folder_path: str) -> None:
            async with semaphore:
                try:
                    async with s3_storage.get_file_stream(doc.file_path) as stream:
                        content = await stream.read()

                    async with zip_lock:
                        relative_path = folder_path.strip("/")
                        zip_entry_path = os.path.join(path_prefix, relative_path, doc.title)

                        await asyncio.to_thread(zip_file.writestr, zip_entry_path, content)

                except Exception as e:
                    logger.error("Ошибка при добавлении документа %s (ID: %s) в архив: %s", doc.title, doc.id, str(e))

        if rows:
            # row[0] - это Document, row[1] - это full_path из CTE
            tasks = [process_document(row[0], row[1]) for row in rows]
            await asyncio.gather(*tasks)
        else:
            logger.info("Папка %s пуста или доступ запрещен, ZIP будет пустым", folder_id)

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
        sort_map = {"name": "name", "created_at": "created_at", "size": "size"}
        target_sort = sort_map.get(sort_by, "created_at")
        direction = "DESC" if order.lower() == "desc" else "ASC"

        f_stmt: Select[Any] = (
            select(
                Folder.id,
                Folder.name.label("name"),
                literal_column("'folder'").label("type"),
                Folder.created_at,
                Folder.parent_id,
                literal_column("0").label("size"),
                literal_column("NULL").label("extension"),
                Folder.created_by_id,
                User.full_name.label("created_by_name"),
            )
            .join(User, Folder.created_by_id == User.id)
            .where(Folder.company_id == company_id)
        )

        d_stmt: Select[Any] = (
            select(
                Document.id,
                Document.title.label("name"),
                literal_column("'file'").label("type"),
                Document.created_at,
                Document.folder_id.label("parent_id"),
                Document.file_size.label("size"),
                Document.file_extension.label("extension"),
                Document.uploaded_by_id.label("created_by_id"),
                User.full_name.label("created_by_name"),
            )
            .join(User, Document.uploaded_by_id == User.id)
            .where(Document.company_id == company_id)
        )

        if user_role == UserRole.EXPERT:
            f_stmt = f_stmt.where(Folder.created_by_id == user_id)
            d_stmt = d_stmt.where(Document.uploaded_by_id == user_id)

        if search:
            f_stmt = f_stmt.where(Folder.name.ilike(f"%{search}%"))
            d_stmt = d_stmt.where(Document.title.ilike(f"%{search}%"))
        else:
            f_stmt = f_stmt.where(Folder.parent_id == folder_id)
            d_stmt = d_stmt.where(Document.folder_id == folder_id)

        if case_id:
            f_stmt = f_stmt.where(Folder.case_id == case_id)
            d_stmt = d_stmt.where(Document.case_id == case_id)

        combined = union_all(f_stmt, d_stmt).alias("entries")

        final_stmt = select(combined).order_by(text(f"{target_sort} {direction}")).limit(limit).offset(offset)

        result = await self.db.execute(final_stmt)

        return [
            FileSystemEntry(
                id=row.id,
                name=row.name,
                type=row.type,
                created_at=row.created_at,
                created_by_id=row.created_by_id,
                created_by_name=row.created_by_name,
                parent_id=row.parent_id,
                size=row.size if row.type == "file" else None,
                extension=row.extension,
            )
            for row in result.all()
        ]

    async def _collect_file_paths_in_tree(
        self,
        root_ids: list[uuid.UUID],
        company_id: uuid.UUID,
    ) -> list[str]:
        paths, _ = await self._collect_file_paths_and_ids_in_tree(root_ids, company_id)
        return paths

    async def _collect_file_paths_and_ids_in_tree(
        self,
        root_ids: list[uuid.UUID],
        company_id: uuid.UUID,
    ) -> tuple[list[str], set[uuid.UUID]]:
        """Возвращает (file_paths, doc_ids) всех документов в поддеревьях root_ids."""
        if not root_ids:
            return [], set()

        base_cte = select(Folder.id).where(Folder.id.in_(root_ids), Folder.company_id == company_id).cte(name="folder_tree", recursive=True)
        recursive_part = select(Folder.id).join(base_cte, Folder.parent_id == base_cte.c.id).where(Folder.company_id == company_id)
        folder_tree = base_cte.union_all(recursive_part)

        docs_stmt = (
            select(Document.id, Document.file_path)
            .join(folder_tree, Document.folder_id == folder_tree.c.id)
            .where(Document.company_id == company_id)
        )
        result = await self.db.execute(docs_stmt)
        rows = result.all()

        return [row[1] for row in rows], {row[0] for row in rows}

    async def _delete_s3_files_safe(self, file_paths: list[str]) -> None:
        """
        удаляет файлы из S3 после коммита БД.
        """
        if not file_paths:
            return

        results = await asyncio.gather(
            *[s3_storage.delete_file(fp) for fp in file_paths],
            return_exceptions=True,
        )

        for fp, result in zip(file_paths, results, strict=True):
            if isinstance(result, Exception):
                logger.error("Не удалось удалить файл из S3 [%s]: %s", fp, result)

    async def _update_subtree_case_id(
        self,
        root_folder_id: uuid.UUID,
        new_case_id: uuid.UUID | None,
        company_id: uuid.UUID,
    ) -> None:
        """
        рекурсивно обновляет case_id у всех дочерних папок и документов.
        """
        base_cte = (
            select(Folder.id).where(Folder.parent_id == root_folder_id, Folder.company_id == company_id).cte(name="subtree", recursive=True)
        )
        recursive_part = select(Folder.id).join(base_cte, Folder.parent_id == base_cte.c.id).where(Folder.company_id == company_id)
        subtree = base_cte.union_all(recursive_part)

        await self.db.execute(
            update(Folder).where(Folder.id.in_(select(subtree.c.id)), Folder.company_id == company_id).values(case_id=new_case_id)
        )

        all_folder_ids_cte = (
            select(Folder.id)
            .where(
                or_(Folder.id == root_folder_id, Folder.id.in_(select(subtree.c.id))),
                Folder.company_id == company_id,
            )
            .cte(name="all_folders")
        )
        await self.db.execute(
            update(Document)
            .where(
                Document.folder_id.in_(select(all_folder_ids_cte.c.id)),
                Document.company_id == company_id,
            )
            .values(case_id=new_case_id)
        )

    async def _is_descendant_folder(
        self,
        potential_parent_id: uuid.UUID,
        folder_id: uuid.UUID,
    ) -> bool:
        """Проверяет, является ли potential_parent_id потомком folder_id."""
        if folder_id == potential_parent_id:
            return False

        base_case = select(Folder.parent_id.label("ancestor_id")).where(Folder.id == potential_parent_id).cte(recursive=True)
        recursive_case = select(Folder.parent_id.label("ancestor_id")).join(base_case, Folder.id == base_case.c.ancestor_id)
        all_ancestors = base_case.union_all(recursive_case)

        stmt = select(1).where(all_ancestors.c.ancestor_id == folder_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def _check_document_access(
        self,
        document: Document,
        user_id: uuid.UUID,
        user_role: UserRole,
        company_id: uuid.UUID,
    ) -> bool:
        if document.company_id != company_id:
            return False
        if user_role in [UserRole.ADMIN, UserRole.CEO, UserRole.ACCOUNTANT]:
            return True
        return document.uploaded_by_id == user_id

    async def _check_folder_access(
        self,
        folder: Folder,
        user_id: uuid.UUID,
        user_role: UserRole,
        company_id: uuid.UUID,
    ) -> bool:
        if folder.company_id != company_id:
            return False
        if user_role in [UserRole.ADMIN, UserRole.CEO, UserRole.ACCOUNTANT]:
            return True
        return folder.created_by_id == user_id

    async def download_bulk_to_zip(
        self,
        zip_file: zipfile.ZipFile,
        folder_ids: list[uuid.UUID] | None,
        document_ids: list[uuid.UUID] | None,
        user_id: uuid.UUID,
        user_role: UserRole,
        company_id: uuid.UUID,
    ) -> None:
        """
        Массовое скачивание папок и документов в один ZIP-архив.
        """
        semaphore = asyncio.Semaphore(5)
        zip_lock = asyncio.Lock()

        tasks = []

        if folder_ids:
            folder_base = select(Folder.id, Folder.name, Folder.parent_id, cast(Folder.name, Text).label("full_path")).where(
                Folder.id.in_(folder_ids), Folder.company_id == company_id
            )

            if user_role == UserRole.EXPERT:
                folder_base = folder_base.where(Folder.created_by_id == user_id)

            folder_cte = folder_base.cte(name="bulk_folder_hierarchy", recursive=True)

            folder_rec = select(Folder.id, Folder.name, Folder.parent_id, (folder_cte.c.full_path + "/" + Folder.name).label("full_path")).join(
                folder_cte, Folder.parent_id == folder_cte.c.id
            )

            if user_role == UserRole.EXPERT:
                folder_rec = folder_rec.where(Folder.created_by_id == user_id)

            folder_union = folder_cte.union_all(folder_rec)
            folder_subquery = folder_union.alias("folder_tree")

            folder_docs_stmt = select(Document, folder_subquery.c.full_path).join(folder_subquery, Document.folder_id == folder_subquery.c.id)

            res = await self.db.execute(folder_docs_stmt)
            for row in res.all():
                tasks.append(self._process_zip_entry(row[0], row[1], zip_file, semaphore, zip_lock))

        if document_ids:
            doc_stmt = select(Document).where(Document.id.in_(document_ids), Document.company_id == company_id)

            if user_role == UserRole.EXPERT:
                doc_stmt = doc_stmt.where(Document.uploaded_by_id == user_id)

            res_docs = await self.db.execute(doc_stmt)
            for doc in res_docs.scalars().all():
                tasks.append(self._process_zip_entry(doc, "", zip_file, semaphore, zip_lock))

        if tasks:
            await asyncio.gather(*tasks)

    async def _process_zip_entry(
        self,
        doc: Document,
        folder_path: str,
        zip_file: zipfile.ZipFile,
        semaphore: asyncio.Semaphore,
        zip_lock: asyncio.Lock,
    ) -> None:
        """Вспомогательный метод для обработки одного файла (S3 -> ZIP)"""
        async with semaphore:
            try:
                async with s3_storage.get_file_stream(doc.file_path) as stream:
                    content = await stream.read()

                async with zip_lock:
                    relative_path = folder_path.strip("/")
                    zip_path = os.path.join(relative_path, doc.title)

                    await asyncio.to_thread(zip_file.writestr, zip_path, content)
            except Exception as e:
                logger.error(f"Ошибка при упаковке файла {doc.id}: {e}")
