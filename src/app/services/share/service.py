import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.core.auth.models import UserContext
from src.app.core.auth.security import hash_password, verify_password
from src.app.services.document.models import Document, Folder, ShareAccessType, ShareType
from src.app.services.share.models import DocumentShare, ShareAccessLog, ShareBatch
from src.app.services.share.schemas import (
    AccessLinkSchema,
    CreateLinkShareSchema,
    CreateUserShareSchema,
    DocumentShareOut,
    ResourceShareInfoOut,
    ShareAccessLinkResult,
    ShareBatchOut,
    ShareRecipientOut,
    ShareResourceSchema,
)
from src.app.services.user.models import User, UserRole

SHARE_BATCH_OPTIONS = [
    selectinload(ShareBatch.shares),
    selectinload(ShareBatch.owner),
    selectinload(ShareBatch.shared_with),
]


class ShareService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_link_share(
        self,
        current_user: UserContext,
        data: CreateLinkShareSchema,
    ) -> ShareBatchOut:
        """
        Создаёт публичную ссылку для одного или нескольких ресурсов.
        Возвращает батч с токеном для формирования URL вида /share/{token}.
        """
        self._assert_can_share(current_user)

        password_hash: str | None = None
        if data.password:
            password_hash = hash_password(data.password)

        batch = ShareBatch(
            company_id=current_user.company_id,
            owner_id=current_user.id,
            share_type=ShareType.LINK,
            link_password_hash=password_hash,
            expires_at=data.expires_at,
            message=data.message,
            is_active=True,
        )
        self.db.add(batch)
        await self.db.flush()

        await self._attach_resources(batch, data.resources, current_user.company_id)
        await self.db.commit()
        await self.db.refresh(batch, ["shares"])

        return self._to_batch_out(batch)

    async def create_user_share(
        self,
        current_user: UserContext,
        data: CreateUserShareSchema,
    ) -> list[ShareBatchOut]:
        self._assert_can_share(current_user)

        recipient_ids = list(dict.fromkeys(data.shared_with_user_ids))

        if current_user.id in recipient_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Нельзя поделиться с самим собой",
            )

        result = await self.db.execute(
            select(User).where(
                User.id.in_(recipient_ids),
                User.company_id == current_user.company_id,
                User.is_active.is_(True),
            )
        )
        found_users = {u.id: u for u in result.scalars().all()}

        missing = [uid for uid in recipient_ids if uid not in found_users]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Сотрудники не найдены в вашей компании: {missing}",
            )

        for res in data.resources:
            if res.document_id:
                await self._assert_document_exists(res.document_id, current_user.company_id)
            elif res.folder_id:
                await self._assert_folder_exists(res.folder_id, current_user.company_id)

        doc_ids = [r.document_id for r in data.resources if r.document_id]
        folder_ids = [r.folder_id for r in data.resources if r.folder_id]

        resource_filter = []
        if doc_ids:
            resource_filter.append(DocumentShare.document_id.in_(doc_ids))
        if folder_ids:
            resource_filter.append(DocumentShare.folder_id.in_(folder_ids))

        existing_shares_result = await self.db.execute(
            select(
                ShareBatch.shared_with_user_id,
                DocumentShare.document_id,
                DocumentShare.folder_id,
            )
            .join(DocumentShare, DocumentShare.batch_id == ShareBatch.id)
            .where(
                ShareBatch.shared_with_user_id.in_(recipient_ids),
                ShareBatch.share_type == ShareType.USER,
                ShareBatch.is_active.is_(True),
                or_(*resource_filter),
            )
        )
        existing_shares = existing_shares_result.all()

        already_shared: set[tuple[Any, Any]] = set()
        for user_id, doc_id, folder_id in existing_shares:
            res_id = doc_id or folder_id
            already_shared.add((user_id, res_id))

        conflicts: list[str] = []
        for recipient_id in recipient_ids:
            recipient_name = found_users[recipient_id].full_name
            for res in data.resources:
                res_id = res.document_id or res.folder_id
                if (recipient_id, res_id) in already_shared:
                    conflicts.append(f"{recipient_name}")

        if conflicts:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Доступ уже был передан ранее: {'; '.join(conflicts)}",
            )

        batches: list[ShareBatch] = []
        for recipient_id in recipient_ids:
            batch = ShareBatch(
                company_id=current_user.company_id,
                owner_id=current_user.id,
                share_type=ShareType.USER,
                shared_with_user_id=recipient_id,
                expires_at=data.expires_at,
                message=data.message,
                share_token=None,
                is_active=True,
            )
            self.db.add(batch)
            await self.db.flush()

            for res in data.resources:
                self.db.add(
                    DocumentShare(
                        batch_id=batch.id,
                        document_id=res.document_id,
                        folder_id=res.folder_id,
                        permission_level=res.permission_level,
                        can_download=res.can_download,
                    )
                )
            batches.append(batch)

        await self.db.commit()

        for batch in batches:
            await self.db.refresh(batch, ["shares"])

        return [self._to_batch_out(b) for b in batches]

    async def get_resource_shares(
        self,
        current_user: UserContext,
        document_id: uuid.UUID | None = None,
        folder_id: uuid.UUID | None = None,
    ) -> ResourceShareInfoOut:
        """
        Возвращает сводку о том, кому передан конкретный документ или папка.
        Используется при клике на файл/папку в интерфейсе (панель «Доступ»).

        Показывает:
          - список сотрудников, которым передан ресурс (USER-шары)
          - список активных публичных ссылок (LINK-шары)
        """
        if not (document_id or folder_id) or (document_id and folder_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Укажите ровно один из параметров: document_id или folder_id",
            )

        resource_id: uuid.UUID
        if document_id:
            await self._assert_document_exists(document_id, current_user.company_id)
            resource_type = "document"
            resource_id = document_id
            filter_col = DocumentShare.document_id
        else:
            assert folder_id is not None
            await self._assert_folder_exists(folder_id, current_user.company_id)
            resource_type = "folder"
            resource_id = folder_id
            filter_col = DocumentShare.folder_id

        result = await self.db.execute(
            select(ShareBatch)
            .join(DocumentShare, DocumentShare.batch_id == ShareBatch.id)
            .where(
                filter_col == resource_id,
                ShareBatch.owner_id == current_user.id,
                ShareBatch.company_id == current_user.company_id,
                ShareBatch.is_active.is_(True),
            )
            .options(
                selectinload(ShareBatch.shares),
                selectinload(ShareBatch.shared_with),
            )
            .distinct()
        )
        batches = result.scalars().all()

        recipients: list[ShareRecipientOut] = []
        public_links: list[ShareBatchOut] = []

        for batch in batches:
            resource_share = next(
                (s for s in batch.shares if (document_id and s.document_id == document_id) or (folder_id and s.folder_id == folder_id)),
                None,
            )
            if resource_share is None:
                continue

            if batch.share_type == ShareType.USER and batch.shared_with:
                recipients.append(
                    ShareRecipientOut(
                        batch_id=batch.id,
                        user_id=batch.shared_with.id,
                        full_name=batch.shared_with.full_name,
                        email=batch.shared_with.email,
                        permission_level=resource_share.permission_level,
                        can_download=resource_share.can_download,
                        expires_at=batch.expires_at,
                        is_active=batch.is_active,
                        shared_at=batch.created_at,
                    )
                )
            elif batch.share_type == ShareType.LINK:
                views = await self._count_access(batch.id, ShareAccessType.VIEW)
                downloads = await self._count_access(batch.id, ShareAccessType.DOWNLOAD)
                public_links.append(self._to_batch_out(batch, current_views=views, current_downloads=downloads))

        return ResourceShareInfoOut(
            resource_id=resource_id,
            resource_type=resource_type,
            recipients=recipients,
            public_links=public_links,
        )

    async def access_link(
        self,
        schema: AccessLinkSchema,
        accessed_by_user_id: uuid.UUID | None = None,
    ) -> ShareAccessLinkResult:
        """Валидирует публичную ссылку и логирует просмотр."""
        batch = await self._get_active_link_batch(schema.token)

        self._assert_not_expired(batch)
        self._assert_password_valid(batch, schema.password)

        await self._log_access(
            batch_id=batch.id,
            access_type=ShareAccessType.VIEW,
            accessed_by_user_id=accessed_by_user_id,
            ip_address=schema.ip_address,
            user_agent=schema.user_agent,
        )

        shares_out = [DocumentShareOut.model_validate(s) for s in batch.shares]
        can_download = any(s.can_download for s in batch.shares)

        return ShareAccessLinkResult(
            batch_id=batch.id,
            shares=shares_out,
            can_download=can_download,
        )

    async def log_download(
        self,
        token: str,
        accessed_by_user_id: uuid.UUID | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Логирует скачивание файла по публичной ссылке."""
        batch = await self._get_active_link_batch(token)
        self._assert_not_expired(batch)

        if not any(s.can_download for s in batch.shares):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Скачивание запрещено для этой ссылки",
            )

        await self._log_access(
            batch_id=batch.id,
            access_type=ShareAccessType.DOWNLOAD,
            accessed_by_user_id=accessed_by_user_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    async def revoke_share(self, current_user: UserContext, batch_id: uuid.UUID) -> None:
        """Деактивирует батч. Только владелец, ADMIN или CEO могут отозвать шар."""
        batch = await self._get_batch_by_id(batch_id, current_user.company_id)
        if not batch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Шар не найден",
            )

        is_owner = batch.owner_id == current_user.id
        is_privileged = current_user.role in (UserRole.ADMIN, UserRole.CEO)

        if not (is_owner or is_privileged):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Недостаточно прав для отзыва доступа",
            )

        batch.is_active = False
        await self.db.commit()

    async def get_my_shares(
        self,
        current_user: UserContext,
        share_type: ShareType | None = None,
        only_active: bool = True,
    ) -> list[ShareBatchOut]:
        """Возвращает все шары, созданные текущим пользователем."""
        q = (
            select(ShareBatch)
            .where(
                ShareBatch.owner_id == current_user.id,
                ShareBatch.company_id == current_user.company_id,
            )
            .options(*SHARE_BATCH_OPTIONS)
            .order_by(ShareBatch.created_at.desc())
        )
        if only_active:
            q = q.where(ShareBatch.is_active.is_(True))
        if share_type is not None:
            q = q.where(ShareBatch.share_type == share_type)

        result = await self.db.execute(q)
        batches = result.scalars().all()
        return [self._to_batch_out(b) for b in batches]

    async def get_shared_with_me(
        self,
        current_user: UserContext,
        only_active: bool = True,
    ) -> list[ShareBatchOut]:
        """Возвращает все USER-шары, адресованные текущему пользователю."""
        q = (
            select(ShareBatch)
            .where(
                ShareBatch.shared_with_user_id == current_user.id,
                ShareBatch.share_type == ShareType.USER,
            )
            .options(*SHARE_BATCH_OPTIONS)
            .order_by(ShareBatch.created_at.desc())
        )
        if only_active:
            q = q.where(ShareBatch.is_active.is_(True))

        result = await self.db.execute(q)
        batches = result.scalars().all()
        return [self._to_batch_out(b) for b in batches]

    async def get_batch_detail(
        self,
        current_user: UserContext,
        batch_id: uuid.UUID,
    ) -> ShareBatchOut:
        """Детали батча со счётчиками. Доступно владельцу, получателю, ADMIN / CEO."""
        batch = await self._get_batch_by_id(batch_id, current_user.company_id)
        if not batch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Шар не найден",
            )

        is_owner = batch.owner_id == current_user.id
        is_recipient = batch.shared_with_user_id == current_user.id
        is_privileged = current_user.role in (UserRole.ADMIN, UserRole.CEO)

        if not (is_owner or is_recipient or is_privileged):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Нет доступа к этому шару",
            )

        views = await self._count_access(batch.id, ShareAccessType.VIEW)
        downloads = await self._count_access(batch.id, ShareAccessType.DOWNLOAD)

        return self._to_batch_out(batch, current_views=views, current_downloads=downloads)

    @staticmethod
    def _assert_can_share(user: UserContext) -> None:
        if user.role == UserRole.EXPERT:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Эксперты не могут создавать общий доступ к файлам",
            )

    @staticmethod
    def _assert_not_expired(batch: ShareBatch) -> None:
        if batch.expires_at and batch.expires_at < datetime.now(tz=UTC):
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Срок действия ссылки истёк",
            )

    @staticmethod
    def _assert_password_valid(batch: ShareBatch, password: str | None) -> None:
        if batch.link_password_hash is None:
            return
        if not password:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Ссылка защищена паролем",
            )
        if not verify_password(password, batch.link_password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Неверный пароль",
            )

    async def _attach_resources(
        self,
        batch: ShareBatch,
        resources: list[ShareResourceSchema],
        company_id: uuid.UUID,
    ) -> None:
        for res in resources:
            if res.document_id:
                await self._assert_document_exists(res.document_id, company_id)
            elif res.folder_id:
                await self._assert_folder_exists(res.folder_id, company_id)
            self.db.add(
                DocumentShare(
                    batch_id=batch.id,
                    document_id=res.document_id,
                    folder_id=res.folder_id,
                    permission_level=res.permission_level,
                    can_download=res.can_download,
                )
            )

    async def _log_access(
        self,
        batch_id: uuid.UUID,
        access_type: ShareAccessType,
        accessed_by_user_id: uuid.UUID | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        self.db.add(
            ShareAccessLog(
                batch_id=batch_id,
                access_type=access_type,
                accessed_by_user_id=accessed_by_user_id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        )
        await self.db.commit()

    async def _count_access(self, batch_id: uuid.UUID, access_type: ShareAccessType) -> int:
        result = await self.db.execute(
            select(func.count(ShareAccessLog.id)).where(
                ShareAccessLog.batch_id == batch_id,
                ShareAccessLog.access_type == access_type,
            )
        )
        return result.scalar_one()

    async def _get_active_link_batch(self, token: str) -> ShareBatch:
        result = await self.db.execute(
            select(ShareBatch)
            .where(
                ShareBatch.share_token == token,
                ShareBatch.share_type == ShareType.LINK,
                ShareBatch.is_active.is_(True),
            )
            .options(selectinload(ShareBatch.shares))
        )
        batch = result.scalar_one_or_none()
        if not batch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Ссылка не найдена или деактивирована",
            )
        return batch

    async def _get_batch_by_id(self, batch_id: uuid.UUID, company_id: uuid.UUID) -> ShareBatch | None:
        result = await self.db.execute(
            select(ShareBatch).where(ShareBatch.id == batch_id, ShareBatch.company_id == company_id).options(*SHARE_BATCH_OPTIONS)
        )
        return result.scalar_one_or_none()

    async def _get_user_in_company(self, user_id: uuid.UUID, company_id: uuid.UUID) -> User | None:
        result = await self.db.execute(
            select(User).where(
                User.id == user_id,
                User.company_id == company_id,
                User.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def _assert_document_exists(self, document_id: uuid.UUID, company_id: uuid.UUID) -> None:
        result = await self.db.execute(select(Document.id).where(Document.id == document_id, Document.company_id == company_id))
        if not result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Документ {document_id} не найден в вашей компании",
            )

    async def _assert_folder_exists(self, folder_id: uuid.UUID, company_id: uuid.UUID) -> None:
        result = await self.db.execute(select(Folder.id).where(Folder.id == folder_id, Folder.company_id == company_id))
        if not result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Папка {folder_id} не найдена в вашей компании",
            )

    @staticmethod
    def _to_batch_out(
        batch: ShareBatch,
        current_views: int = 0,
        current_downloads: int = 0,
    ) -> ShareBatchOut:
        return ShareBatchOut(
            id=batch.id,
            share_type=batch.share_type,
            share_token=batch.share_token,
            expires_at=batch.expires_at,
            is_active=batch.is_active,
            created_at=batch.created_at,
            message=batch.message,
            shared_with_user_id=batch.shared_with_user_id,
            has_password=batch.link_password_hash is not None,
            shares=[DocumentShareOut.model_validate(s) for s in batch.shares],
            current_views=current_views,
            current_downloads=current_downloads,
        )
