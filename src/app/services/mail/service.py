from __future__ import annotations

import asyncio
import io
import logging
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import and_, delete, desc, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from src.app.core.auth.models import UserContext
from src.app.core.config.settings import settings
from src.app.core.schemas.base import PaginatedResponse, PaginationMeta
from src.app.core.storage.s3 import s3_storage
from src.app.services.mail._internal import (
    BulkValues,
    SendContext,
    _AttachmentHelper,
    _MimeBuilder,
    _SmtpHelper,
)
from src.app.services.mail.imap_worker import ImapSyncer
from src.app.services.mail.models import (
    MailAttachment,
    MailContent,
    MailFolder,
    MailMessage,
    MailMessageStatus,
    MailMessageType,
    MailOversizedBatch,
    MailRecipient,
    new_token,
)
from src.app.services.mail.schemas import (
    MailAttachmentRead,
    MailContentCreate,
    MailListItem,
    MailMessageBulkAction,
    MailMessageBulkResult,
    MailMessageCreate,
    MailMessageListItem,
    MailMessageRead,
    MailMessageUpdate,
    MailRecipientCreate,
    MailSendErrorCode,
    MailSendResult,
    MailSingleMessageListItem,
    MailSyncResult,
    MailThreadListItem,
    MailThreadMeta,
    MailThreadRead,
    OversizedBatchOut,
    OversizedFileDownloadOut,
    OversizedZipOut,
    PaginatedMailMessages,
    PaginatedMailThread,
)
from src.app.services.user.models import UserRole

log = logging.getLogger(__name__)

_LIST_OPTS = (
    selectinload(MailMessage.recipients),
    selectinload(MailMessage.attachments),
)
_DETAIL_OPTS = (
    selectinload(MailMessage.content),
    selectinload(MailMessage.recipients),
    selectinload(MailMessage.attachments),
)
_PREVIEW_TTL = 15 * 60
_DOWNLOAD_TTL = 60 * 60
_ZIP_ENTRY_TTL = 2 * 60 * 60


@dataclass
class _UploadedFileInfo:
    file: UploadFile
    key: str
    size: int


class _MailBase:
    def __init__(self, db: AsyncSession, user: UserContext) -> None:
        self._db = db
        self._user = user
        self._company_id = user.company_id

    def _check_access(self) -> None:
        if self._user.role == UserRole.EXPERT:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Пользователи с ролью EXPERT не имеют доступа к почте.",
            )

    def _base_filter(self) -> ColumnElement[bool]:
        return and_(
            MailMessage.company_id == self._company_id,
            MailMessage.is_deleted.is_(False),
        )

    async def _get_or_404(self, message_id: uuid.UUID) -> MailMessage:
        stmt = select(MailMessage).where(MailMessage.id == message_id, self._base_filter()).options(*_DETAIL_OPTS)
        msg = await self._db.scalar(stmt)
        if msg is None:
            raise HTTPException(status_code=404, detail="Сообщение не найдено")
        return msg

    async def _persist_outgoing(
        self,
        *,
        payload: MailMessageCreate,
        external_id: str | None,
        folder: MailFolder,
        msg_status: MailMessageStatus,
        msg_type: MailMessageType,
        thread_id: uuid.UUID | None = None,
        parent_id: uuid.UUID | None = None,
    ) -> MailMessage:
        now = datetime.now(UTC)
        msg = MailMessage(
            id=uuid.uuid4(),
            company_id=self._company_id,
            external_message_id=external_id,
            thread_id=thread_id or uuid.uuid4(),
            parent_id=parent_id,
            user_id=self._user.id,
            case_id=payload.case_id,
            sender_email=payload.sender_email,
            sender_name=payload.sender_name,
            reply_to=payload.reply_to,
            subject=payload.subject,
            folder=folder,
            message_type=msg_type,
            status=msg_status,
            is_read=True,
            size_bytes=len((payload.content.body_text or "").encode()),
            sent_at=now,
            processed_at=now,
            updated_at=now,
        )
        self._db.add(msg)
        await self._db.flush()
        self._db.add(
            MailContent(
                message_id=msg.id,
                body_text=payload.content.body_text,
                body_html=payload.content.body_html,
            )
        )
        self._db.add_all(
            [
                MailRecipient(
                    message_id=msg.id,
                    email_address=r.email_address,
                    recipient_type=r.recipient_type,
                    name=r.name,
                )
                for r in payload.recipients
            ]
        )
        await self._db.flush()
        return msg

    async def _mark_sent(self, message_id: uuid.UUID) -> None:
        """Только помечает статус. Коммит — на совести вызывающего кода."""
        await self._db.execute(
            update(MailMessage).where(MailMessage.id == message_id).values(status=MailMessageStatus.SENT, updated_at=datetime.now(UTC))
        )

    async def _mark_error(self, message_id: uuid.UUID) -> None:
        """Только помечает статус. Коммит — на совести вызывающего кода."""
        await self._db.execute(
            update(MailMessage).where(MailMessage.id == message_id).values(status=MailMessageStatus.ERROR, updated_at=datetime.now(UTC))
        )

    async def _rollback_message(
        self,
        message_id: uuid.UUID,
        attachment_records: list[MailAttachment],
    ) -> None:
        await _AttachmentHelper.rollback_keys([a.s3_key for a in attachment_records])
        await self._db.execute(delete(MailMessage).where(MailMessage.id == message_id))
        await self._db.commit()
        log.info("Rolled back message %s (SMTP size rejection)", message_id)


class MailSendService(_MailBase):
    async def send_message(self, payload: MailMessageCreate) -> MailSendResult:
        self._check_access()
        return await self._execute_send(SendContext(payload=payload))

    async def send_message_with_attachments(
        self,
        payload: MailMessageCreate,
        files: list[UploadFile],
        *,
        max_total_size: int,
        max_count: int,
    ) -> MailSendResult:
        self._check_access()
        actual_files = [f for f in files if f.filename]
        if not actual_files:
            return await self._execute_send(SendContext(payload=payload))
        total = sum(f.size for f in actual_files if f.size is not None)
        if total <= max_total_size:
            return await self._execute_send(
                SendContext(
                    payload=payload,
                    files=actual_files,
                    max_total_size=max_total_size,
                    max_count=max_count,
                )
            )
        smtp_files, oversized_files = _AttachmentHelper.split_by_limit(actual_files, max_total_size)
        log.info(
            "Hybrid send: %d SMTP file(s) / %d oversized file(s)",
            len(smtp_files),
            len(oversized_files),
        )
        return await self._send_hybrid(
            payload=payload,
            smtp_files=smtp_files,
            oversized_files=oversized_files,
            max_total_size=max_total_size,
            max_count=max_count,
        )

    async def reply_to_message(
        self,
        message_id: uuid.UUID,
        payload: MailMessageCreate,
        *,
        reply_all: bool = False,
    ) -> MailSendResult:
        self._check_access()
        original = await self._get_or_404(message_id)
        return await self._execute_send(
            SendContext(
                payload=payload,
                thread_id=original.thread_id,
                parent_id=original.id,
            ),
            original=original,
            reply_all=reply_all,
        )

    async def reply_with_attachments(
        self,
        message_id: uuid.UUID,
        payload: MailMessageCreate,
        files: list[UploadFile],
        *,
        reply_all: bool = False,
        max_total_size: int,
        max_count: int,
    ) -> MailSendResult:
        self._check_access()
        original = await self._get_or_404(message_id)
        return await self._execute_send(
            SendContext(
                payload=payload,
                thread_id=original.thread_id,
                parent_id=original.id,
                files=files,
                max_total_size=max_total_size,
                max_count=max_count,
            ),
            original=original,
            reply_all=reply_all,
        )

    async def forward_message(
        self,
        message_id: uuid.UUID,
        payload: MailMessageCreate,
    ) -> MailSendResult:
        self._check_access()
        original = await self._get_or_404(message_id)
        return await self._execute_send(
            SendContext(payload=_MimeBuilder.build_forward_payload(payload, original)),
            original=original,
            is_forward=True,
        )

    async def forward_with_attachments(
        self,
        message_id: uuid.UUID,
        payload: MailMessageCreate,
        files: list[UploadFile],
        *,
        max_total_size: int,
        max_count: int,
    ) -> MailSendResult:
        self._check_access()
        original = await self._get_or_404(message_id)
        return await self._execute_send(
            SendContext(
                payload=_MimeBuilder.build_forward_payload(payload, original),
                files=files,
                max_total_size=max_total_size,
                max_count=max_count,
            ),
            original=original,
            is_forward=True,
        )

    async def _send_hybrid(
        self,
        payload: MailMessageCreate,
        smtp_files: list[UploadFile],
        oversized_files: list[UploadFile],
        *,
        max_total_size: int,
        max_count: int,
    ) -> MailSendResult:
        share_token = new_token()
        base_url = payload.frontend_domain
        uploaded: list[_UploadedFileInfo] = []
        attachment_ids: list[uuid.UUID] = []
        for file in oversized_files:
            if not file.filename:
                continue
            ext = ("." + file.filename.rsplit(".", 1)[-1].lower()) if "." in file.filename else ""
            key = f"mail/oversized/{self._company_id}/{uuid.uuid4()}{ext}"
            size = await s3_storage.upload_file_multipart(
                upload=file,
                object_key=key,
                content_type=file.content_type or "application/octet-stream",
            )
            uploaded.append(_UploadedFileInfo(file=file, key=key, size=size))
            attachment_ids.append(uuid.uuid4())

        share_url = f"{base_url}/mail/oversized/{share_token}"
        oversized_names_text = "\n".join(f"  • {item.file.filename}" for item in uploaded if item.file.filename)
        oversized_names_html = "".join(f"<li>{item.file.filename}</li>" for item in uploaded if item.file.filename)
        text_block = f"\n📎 Дополнительные вложения:\n{oversized_names_text}\nСкачать: {share_url}"
        html_block = (
            f"<br><hr>"
            f"<p><b>📎 Дополнительные вложения:</b></p>"
            f"<ul>{oversized_names_html}</ul>"
            f'<p><a href="{share_url}">Открыть и скачать файлы →</a></p>'
        )
        updated_payload = payload.model_copy(
            update={
                "content": MailContentCreate(
                    body_text=(payload.content.body_text or "") + text_block,
                    body_html=(payload.content.body_html or "") + html_block,
                )
            }
        )
        result = await self._execute_send(
            SendContext(
                payload=updated_payload,
                files=smtp_files,
                max_total_size=max_total_size,
                max_count=max_count,
            )
        )
        if not result.message_id:
            log.warning(
                "Hybrid send failed, rolling back %d oversized S3 object(s)",
                len(uploaded),
            )
            await _AttachmentHelper.rollback_keys([item.key for item in uploaded])
            return result

        batch = MailOversizedBatch(
            mail_message_id=result.message_id,
            share_token=share_token,
            is_active=True,
            company_id=self._company_id,
        )
        self._db.add(batch)
        await self._db.flush()
        for att_id, item in zip(attachment_ids, uploaded, strict=True):
            self._db.add(
                MailAttachment(
                    id=att_id,
                    company_id=self._company_id,
                    mail_message_id=result.message_id,
                    batch_id=batch.id,
                    s3_key=item.key,
                    s3_bucket=settings.S3_BUCKET_NAME,
                    filename=item.file.filename,
                    file_size=item.size,
                    content_type=item.file.content_type or "application/octet-stream",
                )
            )
        await self._db.commit()
        result.oversized_links = True
        return result

    async def _execute_send(
        self,
        ctx: SendContext,
        *,
        original: MailMessage | None = None,
        reply_all: bool = False,
        is_forward: bool = False,
    ) -> MailSendResult:
        attachment_helper = _AttachmentHelper(self._company_id)
        actual_files = [f for f in ctx.files if f.filename]
        if actual_files:
            _AttachmentHelper.validate(actual_files, ctx.max_total_size, ctx.max_count)

        mime = _MimeBuilder.build(ctx.payload)
        if original is not None:
            if is_forward:
                _MimeBuilder.decorate_forward(mime, original)
            else:
                _MimeBuilder.decorate_reply(mime, original, reply_all=reply_all)

        external_id: str = mime["Message-ID"]
        msg = await self._persist_outgoing(
            payload=ctx.payload,
            external_id=external_id,
            folder=ctx.folder,
            msg_status=ctx.msg_status,
            msg_type=ctx.msg_type,
            thread_id=ctx.thread_id,
            parent_id=ctx.parent_id,
        )

        attachment_records: list[MailAttachment] = []
        if actual_files:
            try:
                attachment_records = await attachment_helper.upload(msg.id, actual_files, ctx.max_total_size)
            except HTTPException:
                await self._mark_error(msg.id)
                await self._db.commit()
                raise
            self._db.add_all(attachment_records)
            await self._db.flush()
            await _AttachmentHelper.attach_to_mime(mime, actual_files)

        if is_forward and original:
            await _AttachmentHelper.attach_from_s3(mime, original)

        try:
            await _SmtpHelper.send(mime)
        except Exception as exc:
            return await self._handle_smtp_error(
                exc,
                msg_id=msg.id,
                # outbox_id удален, так как MailOutbox больше не используется
                actual_files=actual_files,
                attachment_records=attachment_records,
                max_total_size=ctx.max_total_size,
                original=original,
                is_forward=is_forward,
            )

        # ↓ SMTP подтвердил приём — обновляем статус сообщения
        await self._mark_sent(msg.id)
        return MailSendResult(
            message_id=msg.id,
            status=MailMessageStatus.SENT,
            external_message_id=external_id,
            sent_at=msg.sent_at,
        )

    async def _handle_smtp_error(
        self,
        exc: Exception,
        *,
        msg_id: uuid.UUID,
        # outbox_id: uuid.UUID,  # Удалено
        actual_files: list[UploadFile],
        attachment_records: list[MailAttachment],
        max_total_size: int,
        original: MailMessage | None,
        is_forward: bool,
    ) -> MailSendResult:
        log.error("SMTP send failed: %s", exc)
        if not _SmtpHelper.is_size_error(exc):
            # Письмо не ушло, статус ERROR
            # (воркеру тут делать нечего, ошибка зафиксирована в mail_messages)
            await self._mark_error(msg_id)
            return MailSendResult(
                message_id=msg_id,
                status=MailMessageStatus.ERROR,
                error=str(exc),
            )

        # Size-ошибка: _rollback_message удаляет MailMessage
        await self._rollback_message(msg_id, attachment_records)

        if is_forward and original and not actual_files:
            return MailSendResult(
                message_id=None,
                status=MailMessageStatus.ERROR,
                error_code=MailSendErrorCode.SMTP_SIZE_EXCEEDED,
                error="Почтовый сервер отклонил пересылку: вложения оригинального письма слишком большие.",
                rejected_files=[a.filename for a in original.attachments],
            )

        files_to_remove, suggested_mb = _AttachmentHelper.suggest_removals(actual_files, max_total_size)
        return MailSendResult(
            message_id=None,
            status=MailMessageStatus.ERROR,
            error_code=MailSendErrorCode.SMTP_SIZE_EXCEEDED,
            error=f"Почтовый сервер отклонил письмо: вложения слишком большие. Попробуйте уложиться в {suggested_mb} МБ суммарно.",
            rejected_files=files_to_remove,
        )

    async def send_message_with_links(
        self,
        payload: MailMessageCreate,
        files: list[UploadFile],
    ) -> MailSendResult:
        """
        Все файлы идут в oversized (старый путь: суммарный размер >> лимита).
        Используется когда вообще нечего слать по SMTP.
        """
        share_token = new_token()
        base_url = payload.frontend_domain
        uploaded: list[_UploadedFileInfo] = []
        for file in files:
            if not file.filename:
                continue
            ext = ("." + file.filename.rsplit(".", 1)[-1].lower()) if "." in file.filename else ""
            key = f"mail/oversized/{self._company_id}/{uuid.uuid4()}{ext}"
            size = await s3_storage.upload_file_multipart(
                upload=file,
                object_key=key,
                content_type=file.content_type or "application/octet-stream",
            )
            uploaded.append(_UploadedFileInfo(file=file, key=key, size=size))

        attachment_ids = [uuid.uuid4() for _ in uploaded]
        share_url = f"{base_url}/mail/oversized/{share_token}"
        oversized_names_text = "\n".join(f"  • {item.file.filename}" for item in uploaded if item.file.filename)
        oversized_names_html = "".join(f"<li>{item.file.filename}</li>" for item in uploaded if item.file.filename)
        text_block = f"\n📎 Вложения (превысили лимит, доступны по ссылке):\n{oversized_names_text}\nСкачать: {share_url}"
        html_block = (
            f"<br><hr>"
            f"<p><b>📎 Вложения (превысили лимит почтового сервера):</b></p>"
            f"<ul>{oversized_names_html}</ul>"
            f'<p><a href="{share_url}">Открыть и скачать файлы →</a></p>'
        )
        updated_payload = payload.model_copy(
            update={
                "content": MailContentCreate(
                    body_text=(payload.content.body_text or "") + text_block,
                    body_html=(payload.content.body_html or "") + html_block,
                )
            }
        )
        result = await self._execute_send(SendContext(payload=updated_payload))
        if not result.message_id:
            log.warning(
                "send_message_with_links failed, rolling back %d oversized S3 object(s)",
                len(uploaded),
            )
            await _AttachmentHelper.rollback_keys([item.key for item in uploaded])
            return result

        batch = MailOversizedBatch(
            mail_message_id=result.message_id,
            share_token=share_token,
            is_active=True,
            company_id=self._company_id,
        )
        self._db.add(batch)
        await self._db.flush()
        for att_id, item in zip(attachment_ids, uploaded, strict=True):
            self._db.add(
                MailAttachment(
                    id=att_id,
                    company_id=self._company_id,
                    mail_message_id=result.message_id,
                    batch_id=batch.id,
                    s3_key=item.key,
                    s3_bucket=settings.S3_BUCKET_NAME,
                    filename=item.file.filename,
                    file_size=item.size,
                    content_type=item.file.content_type or "application/octet-stream",
                )
            )
        await self._db.commit()
        result.oversized_links = True
        return result


class MailDraftService(_MailBase):
    async def create_draft(self, payload: MailMessageCreate) -> MailMessageRead:
        self._check_access()
        msg = await self._persist_outgoing(
            payload=payload,
            external_id=None,
            folder=MailFolder.DRAFTS,
            msg_status=MailMessageStatus.DRAFT,
            msg_type=MailMessageType.OUTGOING,
        )
        await self._db.commit()
        return await self._read_message(msg.id)

    async def update_draft(
        self,
        message_id: uuid.UUID,
        payload: MailMessageCreate,
    ) -> MailMessageRead:
        self._check_access()
        msg = await self._get_or_404(message_id)
        if msg.folder != MailFolder.DRAFTS:
            raise HTTPException(status_code=400, detail="Message is not a draft")
        await self._db.execute(
            update(MailMessage)
            .where(MailMessage.id == message_id)
            .values(
                subject=payload.subject,
                sender_email=payload.sender_email,
                sender_name=payload.sender_name,
                updated_at=datetime.now(UTC),
            )
        )
        await self._db.execute(
            update(MailContent)
            .where(MailContent.message_id == message_id)
            .values(body_text=payload.content.body_text, body_html=payload.content.body_html)
        )
        await self._db.execute(delete(MailRecipient).where(MailRecipient.message_id == message_id))
        self._db.add_all(
            [
                MailRecipient(
                    message_id=message_id,
                    email_address=r.email_address,
                    recipient_type=r.recipient_type,
                    name=r.name,
                )
                for r in payload.recipients
            ]
        )
        await self._db.flush()
        await self._db.commit()
        return await self._read_message(message_id)

    async def send_draft(self, message_id: uuid.UUID) -> MailSendResult:
        self._check_access()
        msg = await self._get_or_404(message_id)
        if msg.folder != MailFolder.DRAFTS:
            raise HTTPException(status_code=400, detail="Message is not a draft")
        payload = self._draft_to_payload(msg)
        mime = _MimeBuilder.build(payload)
        external_id: str = mime["Message-ID"]
        now = datetime.now(UTC)
        await self._db.execute(
            update(MailMessage)
            .where(MailMessage.id == message_id)
            .values(
                folder=MailFolder.SENT,
                status=MailMessageStatus.SENDING,
                external_message_id=external_id,
                sent_at=now,
                updated_at=now,
            )
        )
        await self._db.flush()
        try:
            await _SmtpHelper.send(mime)
        except Exception as exc:
            log.error("SMTP send draft failed: %s", exc)
            await self._mark_error(message_id)
            return MailSendResult(
                message_id=message_id,
                status=MailMessageStatus.ERROR,
                error=str(exc),
            )
        await self._mark_sent(message_id)
        return MailSendResult(
            message_id=message_id,
            status=MailMessageStatus.SENT,
            external_message_id=external_id,
            sent_at=now,
        )

    @staticmethod
    def _draft_to_payload(msg: MailMessage) -> MailMessageCreate:
        return MailMessageCreate(
            sender_email=msg.sender_email,
            sender_name=msg.sender_name,
            subject=msg.subject,
            recipients=[
                MailRecipientCreate(
                    email_address=r.email_address,
                    recipient_type=r.recipient_type,
                    name=r.name,
                )
                for r in msg.recipients
            ],
            content=MailContentCreate(
                body_text=msg.content.body_text if msg.content else None,
                body_html=msg.content.body_html if msg.content else None,
            ),
            frontend_domain=None,
        )

    async def _read_message(self, message_id: uuid.UUID) -> MailMessageRead:
        return MailMessageRead.model_validate(await self._get_or_404(message_id))


class MailThreadService(_MailBase):
    async def get_thread(self, thread_id: uuid.UUID) -> MailThreadRead:
        self._check_access()
        rows = await self._db.scalars(
            select(MailMessage)
            .where(MailMessage.thread_id == thread_id, self._base_filter())
            .options(*_DETAIL_OPTS)
            .order_by(MailMessage.sent_at.asc().nullsfirst())
        )
        messages = rows.all()
        if not messages:
            raise HTTPException(status_code=404, detail="Thread not found")
        last = messages[-1]
        return MailThreadRead(
            thread_id=thread_id,
            subject=messages[0].subject,
            message_count=len(messages),
            unread_count=sum(1 for m in messages if not m.is_read),
            last_message_at=last.sent_at or last.processed_at,
            participants=list({m.sender_email for m in messages}),
            messages=[MailMessageRead.model_validate(m) for m in messages],
        )

    async def get_thread_paginated(
        self,
        thread_id: uuid.UUID,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedMailThread:
        self._check_access()
        base_where = and_(MailMessage.thread_id == thread_id, self._base_filter())
        total: int = await self._db.scalar(select(func.count()).select_from(MailMessage).where(base_where)) or 0
        if total == 0:
            raise HTTPException(status_code=404, detail="Thread not found")
        unread_count: int = (
            await self._db.scalar(select(func.count()).select_from(MailMessage).where(base_where, MailMessage.is_read.is_(False))) or 0
        )
        first_msg = await self._db.scalar(select(MailMessage).where(base_where).order_by(MailMessage.sent_at.asc().nullsfirst()).limit(1))
        last_msg = await self._db.scalar(select(MailMessage).where(base_where).order_by(MailMessage.sent_at.desc().nullslast()).limit(1))
        participant_rows = await self._db.scalars(select(MailMessage.sender_email).where(base_where).distinct())
        meta = MailThreadMeta(
            thread_id=thread_id,
            subject=first_msg.subject if first_msg else None,
            message_count=total,
            unread_count=unread_count,
            last_message_at=(last_msg.sent_at or last_msg.processed_at) if last_msg else None,
            participants=list(participant_rows.all()),
        )
        offset = (page - 1) * page_size
        page_rows = await self._db.scalars(
            select(MailMessage)
            .where(base_where)
            .options(*_DETAIL_OPTS)
            .order_by(MailMessage.sent_at.asc().nullsfirst())
            .offset(offset)
            .limit(page_size)
        )
        return PaginatedMailThread(
            meta=meta,
            items=[MailMessageRead.model_validate(m) for m in page_rows.all()],
            total=total,
            page=page,
            page_size=page_size,
            has_next=(offset + page_size) < total,
        )


class MailAttachmentService(_MailBase):
    async def get_list_attachments(self, message_id: uuid.UUID) -> list[MailAttachmentRead]:
        self._check_access()
        await self._get_or_404(message_id)
        stmt = select(MailAttachment).where(MailAttachment.mail_message_id == message_id, MailAttachment.batch_id.is_(None))
        rows = await self._db.scalars(stmt)
        return [MailAttachmentRead.model_validate(a) for a in rows.all()]

    # async def get_list_attachments_files(self, mail_attachment: MailAttachment) -> list[MailAttachmentRead]: ...

    async def get_presigned_download_url(
        self,
        message_id: uuid.UUID,
        attachment_id: uuid.UUID,
        *,
        download: bool = False,
    ) -> str:
        self._check_access()
        await self._get_or_404(message_id)
        att = await self._db.scalar(
            select(MailAttachment).where(
                MailAttachment.id == attachment_id,
                MailAttachment.mail_message_id == message_id,
                MailAttachment.company_id == self._company_id,
            )
        )
        if att is None:
            raise HTTPException(status_code=404, detail="Вложение не найдено")
        return await s3_storage.get_presigned_url(
            object_key=att.s3_key,
            original_filename=att.filename,
            expires_in=3600,
            download=download,
        )


class MailSyncService(_MailBase):
    async def sync_folder(
        self,
        folder: MailFolder,
        days_history: int | None = None,
    ) -> MailSyncResult:
        self._check_access()
        syncer = ImapSyncer(self._db, self._user.company_id, self._user.id)
        return await syncer.sync_folder(folder, days_history=days_history)

    async def sync_all_folders(self) -> list[MailSyncResult]:
        self._check_access()
        syncer = ImapSyncer(db=self._db, company_id=self._company_id, user_id=self._user.id)
        raw = await asyncio.gather(
            *[syncer.sync_folder(f) for f in MailFolder],
            return_exceptions=True,
        )
        now = datetime.now(UTC)
        out: list[MailSyncResult] = []
        for folder, result in zip(MailFolder, raw, strict=True):
            if isinstance(result, Exception):
                log.error("sync_folder(%s) error: %s", folder, result)
                out.append(MailSyncResult(folder=folder, fetched=0, skipped=0, errors=1, synced_at=now))
            else:
                out.append(result)  # type: ignore[arg-type]
        return out


class MailMessageService(_MailBase):
    _BULK_MAP: dict[str, BulkValues] = {
        "read": {"is_read": True},
        "unread": {"is_read": False},
        "star": {"is_starred": True},
        "unstar": {"is_starred": False},
        "important": {"is_important": True},
        "unimportant": {"is_important": False},
        "archive": {"is_archived": True},
        "unarchive": {"is_archived": False},
        "spam": {"is_spam": True},
        "not_spam": {"is_spam": False},
        "trash": {"is_deleted": True, "folder": MailFolder.TRASH},
    }

    async def list_threads(
        self,
        *,
        folder: MailFolder,
        is_read: bool | None = None,
        is_important: bool | None = None,
        is_starred: bool | None = None,
        case_id: uuid.UUID | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> PaginatedResponse[MailListItem]:
        self._check_access()
        base_conditions = [
            self._base_filter(),
            MailMessage.folder == folder,
            not MailMessage.is_deleted,
        ]
        if is_read is not None:
            base_conditions.append(MailMessage.is_read == is_read)
        if is_starred is not None:
            base_conditions.append(MailMessage.is_starred == is_starred)
        if case_id is not None:
            base_conditions.append(MailMessage.case_id == case_id)
        if is_important is not None:
            base_conditions.append(MailMessage.is_important == is_important)
        if search:
            base_conditions.append(MailMessage.subject.ilike(f"%{search}%"))

        where_clause = and_(*[c for c in base_conditions if isinstance(c, ColumnElement)])
        total_query = select(func.count(func.distinct(MailMessage.thread_id))).where(where_clause)
        total: int = await self._db.scalar(total_query) or 0
        subq = (
            select(
                MailMessage,
                func.count().over(partition_by=MailMessage.thread_id).label("total_in_thread"),
                func.count().filter(MailMessage.is_read.is_(False)).over(partition_by=MailMessage.thread_id).label("unread_in_thread"),
                func.row_number()
                .over(
                    partition_by=MailMessage.thread_id,
                    order_by=desc(MailMessage.sent_at),
                )
                .label("rn"),
            ).where(where_clause)
        ).subquery()
        stmt = select(subq).where(subq.c.rn == 1).order_by(desc(subq.c.sent_at)).offset((page - 1) * page_size).limit(page_size)
        rows = (await self._db.execute(stmt)).mappings().all()
        items: list[MailListItem] = []
        for row in rows:
            common = dict(
                id=row["thread_id"],
                subject=row["subject"],
                last_message_at=row["sent_at"] or row["processed_at"],
                unread_count=row["unread_in_thread"],
                sender_name=row["sender_name"],
                sender_email=row["sender_email"],
                snippet=None,
                is_starred=row["is_starred"],
                is_important=row["is_important"],
                has_attachments=False,
            )
            if row["total_in_thread"] == 1:
                items.append(MailSingleMessageListItem(**common, message_id=row["id"]))
            else:
                items.append(MailThreadListItem(**common, message_count=row["total_in_thread"]))

        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        return PaginatedResponse(
            items=items,
            meta=PaginationMeta(
                total_items=total,
                total_pages=total_pages,
                current_page=page,
                per_page=page_size,
                has_next=page < total_pages,
                has_prev=page > 1,
            ),
        )

    async def search_messages(
        self,
        *,
        q: str,
        page: int = 1,
        page_size: int = 50,
    ) -> PaginatedMailMessages:
        self._check_access()
        pattern = f"%{q}%"
        where_clause = and_(
            self._base_filter(),
            or_(
                MailMessage.subject.ilike(pattern),
                MailContent.body_text.ilike(pattern),
            ),
        )
        total: int = await self._db.scalar(select(func.count()).select_from(MailMessage).where(where_clause)) or 0
        offset = (page - 1) * page_size
        rows = await self._db.scalars(
            select(MailMessage)
            .where(where_clause)
            .options(*_LIST_OPTS)
            .order_by(MailMessage.sent_at.desc().nullslast())
            .offset(offset)
            .limit(page_size)
        )
        return PaginatedMailMessages(
            items=[_to_list_item(m) for m in rows.all()],
            total=total,
            page=page,
            page_size=page_size,
            has_next=(offset + page_size) < total,
        )

    async def get_message(self, message_id: uuid.UUID) -> MailMessageRead:
        self._check_access()
        await self._db.execute(
            update(MailMessage)
            .where(
                MailMessage.id == message_id,
                self._base_filter(),
                MailMessage.is_read.is_(False),
            )
            .values(is_read=True, updated_at=datetime.now(UTC))
        )
        await self._db.flush()
        await self._db.commit()
        return MailMessageRead.model_validate(await self._get_or_404(message_id))

    async def update_message(
        self,
        message_id: uuid.UUID,
        payload: MailMessageUpdate,
    ) -> MailMessageRead:
        self._check_access()
        update_data = payload.model_dump(exclude_none=True)
        if not update_data:
            return await self.get_message(message_id)
        update_data["updated_at"] = datetime.now(UTC)
        result = await self._db.execute(
            update(MailMessage).where(MailMessage.id == message_id, self._base_filter()).values(**update_data).returning(MailMessage.id)
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Message not found")
        await self._db.flush()
        await self._db.commit()
        return await self.get_message(message_id)

    async def delete_message(
        self,
        message_id: uuid.UUID,
        *,
        permanent: bool = False,
    ) -> None:
        self._check_access()
        if permanent:
            s3_keys = await self._collect_s3_keys([message_id])
            await self._db.execute(delete(MailMessage).where(MailMessage.id == message_id, self._base_filter()))
            await self._db.flush()
            await _AttachmentHelper.rollback_keys(s3_keys)
        else:
            await self._db.execute(
                update(MailMessage)
                .where(MailMessage.id == message_id, self._base_filter())
                .values(is_deleted=True, folder=MailFolder.TRASH, updated_at=datetime.now(UTC))
            )
            await self._db.flush()

    async def purge_trash(self) -> int:
        self._check_access()
        rows = await self._db.scalars(
            select(MailMessage.id).where(
                MailMessage.company_id == self._company_id,
                MailMessage.is_deleted.is_(True),
            )
        )
        message_ids = list(rows.all())
        if not message_ids:
            return 0
        s3_keys = await self._collect_s3_keys(message_ids)
        await self._db.execute(delete(MailMessage).where(MailMessage.id.in_(message_ids)))
        await self._db.flush()
        await _AttachmentHelper.rollback_keys(s3_keys)
        return len(message_ids)

    async def _collect_s3_keys(self, message_ids: list[uuid.UUID]) -> list[str]:
        rows = await self._db.scalars(select(MailAttachment.s3_key).where(MailAttachment.mail_message_id.in_(message_ids)))
        return list(rows.all())

    async def bulk_action(self, payload: MailMessageBulkAction) -> MailMessageBulkResult:
        self._check_access()
        values = self._BULK_MAP.get(payload.action)
        if not values:
            return MailMessageBulkResult(updated=0, failed=list(payload.message_ids))
        result = await self._db.execute(
            update(MailMessage)
            .where(
                MailMessage.id.in_(payload.message_ids),
                MailMessage.company_id == self._company_id,
            )
            .values(**values, updated_at=datetime.now(UTC))
            .returning(MailMessage.id)
        )
        updated_ids = {row[0] for row in result.fetchall()}
        await self._db.flush()
        return MailMessageBulkResult(
            updated=len(updated_ids),
            failed=[mid for mid in payload.message_ids if mid not in updated_ids],
        )

    async def move_message(
        self,
        message_id: uuid.UUID,
        folder: MailFolder,
    ) -> MailMessageRead:
        self._check_access()
        result = await self._db.execute(
            update(MailMessage)
            .where(MailMessage.id == message_id, self._base_filter())
            .values(folder=folder, updated_at=datetime.now(UTC))
            .returning(MailMessage.id)
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Message not found")
        await self._db.flush()
        return await self.get_message(message_id)

    async def get_stats(self) -> dict[str, int]:
        self._check_access()
        rows = await self._db.execute(
            select(MailMessage.folder, func.count().label("cnt"))
            .where(
                MailMessage.company_id == self._company_id,
                MailMessage.is_read.is_(False),
                MailMessage.is_deleted.is_(False),
            )
            .group_by(MailMessage.folder)
        )
        stats: dict[str, int] = {f.value: 0 for f in MailFolder}
        for folder, cnt in rows.all():
            stats[folder if isinstance(folder, str) else folder.value] = cnt
        return stats


def _to_list_item(msg: MailMessage) -> MailMessageListItem:
    att_count = len(msg.attachments) if msg.attachments else 0
    return MailMessageListItem(
        id=msg.id,
        external_message_id=msg.external_message_id,
        thread_id=msg.thread_id,
        parent_id=msg.parent_id,
        user_id=msg.user_id,
        case_id=msg.case_id,
        sender_email=msg.sender_email,
        sender_name=msg.sender_name,
        reply_to=msg.reply_to,
        subject=msg.subject,
        folder=msg.folder,
        message_type=msg.message_type,
        status=msg.status,
        is_read=msg.is_read,
        is_important=msg.is_important,
        is_starred=msg.is_starred,
        is_spam=msg.is_spam,
        is_archived=msg.is_archived,
        is_deleted=msg.is_deleted,
        size_bytes=msg.size_bytes,
        imap_uid=msg.imap_uid,
        sent_at=msg.sent_at,
        processed_at=msg.processed_at,
        updated_at=msg.updated_at,
        recipients=[
            {
                "id": r.id,
                "email_address": r.email_address,
                "recipient_type": r.recipient_type,
                "name": r.name,
            }
            for r in (msg.recipients or [])
        ],
        attachment_count=att_count,
        has_attachments=att_count > 0,
    )


class MailOversizedService(_MailBase):
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_batch(self, token: str) -> OversizedBatchOut:
        """
        Resolve a share token and return the file list.
        Called by the frontend on page load.
        """
        batch = await self._get_active_batch(token)
        return OversizedBatchOut.model_validate(batch)

    async def get_download_url(self, token: str, file_id: uuid.UUID) -> OversizedFileDownloadOut:
        """
        Generate a short-lived presigned URL for downloading a single file
        (Content-Disposition: attachment).
        """
        batch = await self._get_active_batch(token)
        file = self._find_file(batch, file_id)
        url = await s3_storage.get_presigned_url(
            object_key=file.s3_key,
            original_filename=file.filename,
            expires_in=_DOWNLOAD_TTL,
            download=True,
        )
        return OversizedFileDownloadOut(
            file_id=file.id,
            filename=file.filename,
            url=url,
            expires_in=_DOWNLOAD_TTL,
        )

    async def get_preview_url(self, token: str, file_id: uuid.UUID) -> OversizedFileDownloadOut:
        batch = await self._get_active_batch(token)
        file = self._find_file(batch, file_id)
        url = await s3_storage.get_presigned_url(
            object_key=file.s3_key,
            original_filename=file.filename,
            expires_in=_PREVIEW_TTL,
            download=False,
        )
        return OversizedFileDownloadOut(
            file_id=file.id,
            filename=file.filename,
            url=url,
            expires_in=_PREVIEW_TTL,
        )

    async def get_all_download_urls(self, token: str) -> OversizedZipOut:
        """
        Return presigned download URLs for every file in the batch.
        Used when the frontend wants to trigger parallel individual downloads
        or build its own ZIP client-side.
        """
        batch = await self._get_active_batch(token)
        items: list[OversizedFileDownloadOut] = []
        for file in batch.files:
            url = await s3_storage.get_presigned_url(
                object_key=file.s3_key,
                original_filename=file.filename,
                expires_in=_DOWNLOAD_TTL,
                download=True,
            )
            items.append(
                OversizedFileDownloadOut(
                    file_id=file.id,
                    filename=file.filename,
                    url=url,
                    expires_in=_DOWNLOAD_TTL,
                )
            )
        return OversizedZipOut(files=items)

    async def stream_zip(self, token: str) -> tuple[bytes, str]:
        batch = await self._get_active_batch(token)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file in batch.files:
                try:
                    file_bytes = await s3_storage.get_file_content(file.s3_key)
                    zf.writestr(file.filename, file_bytes)
                except Exception as exc:
                    log.warning(
                        "stream_zip: cannot fetch %s (%s): %s",
                        file.filename,
                        file.s3_key,
                        exc,
                    )
        zip_bytes = buf.getvalue()
        zip_name = f"files_{batch.share_token[:8]}.zip"
        return zip_bytes, zip_name

    async def _get_active_batch(self, token: str) -> MailOversizedBatch:
        batch = await self._db.scalar(
            select(MailOversizedBatch)
            .where(
                MailOversizedBatch.share_token == token,
                MailOversizedBatch.is_active.is_(True),
            )
            .options(selectinload(MailOversizedBatch.files))
        )
        if batch is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Ссылка не найдена или деактивирована.",
            )
        return batch

    @staticmethod
    def _find_file(batch: MailOversizedBatch, file_id: uuid.UUID) -> MailAttachment:
        for f in batch.files:
            if f.id == file_id:
                return f
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Файл не найден в этом батче.",
        )


class MailService(MailSendService, MailDraftService, MailThreadService, MailAttachmentService, MailSyncService, MailMessageService):
    """
    Unified mail service.
    Inherits all domain sub-services. Python's MRO guarantees that
    __init__(db, user) from _MailBase is called exactly once.
    Endpoints import only this class — internal decomposition is invisible.
    """
