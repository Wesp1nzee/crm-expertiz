from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import getaddresses

import aiosmtplib
from fastapi import HTTPException, UploadFile

from src.app.core.config import settings as app_settings
from src.app.core.storage.s3 import s3_storage
from src.app.services.mail.models import (
    MailAttachment,
    MailFolder,
    MailMessage,
    MailMessageStatus,
    MailMessageType,
    MailRecipientType,
)
from src.app.services.mail.schemas import (
    MailContentCreate,
    MailMessageCreate,
    MailRecipientCreate,
)

log = logging.getLogger(__name__)

type BulkValues = dict[str, object]


@dataclass(slots=True)
class SendContext:
    """All parameters needed to persist and deliver one outgoing message."""

    payload: MailMessageCreate
    folder: MailFolder = MailFolder.SENT
    msg_status: MailMessageStatus = MailMessageStatus.SENDING
    msg_type: MailMessageType = MailMessageType.OUTGOING
    thread_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    files: list[UploadFile] = field(default_factory=list)
    max_total_size: int = 25 * 1024 * 1024
    max_count: int = 10


class _MimeBuilder:
    """Builds and decorates MIMEMultipart objects. Stateless — all classmethods."""

    @staticmethod
    def build(payload: MailMessageCreate) -> MIMEMultipart:
        """Construct a fresh MIME envelope from a MailMessageCreate payload."""
        domain = app_settings.MAIL_EMAIL.split("@")[-1]
        mime = MIMEMultipart("mixed")
        mime["Message-ID"] = f"<{uuid.uuid4()}@{domain}>"
        mime["Subject"] = payload.subject or ""
        mime["From"] = f"<{app_settings.MAIL_EMAIL}>"

        def _fmt(recipients: list[MailRecipientCreate]) -> str:
            return ", ".join(f"{r.name} <{r.email_address}>" if r.name else f"<{r.email_address}>" for r in recipients)

        header_map = {
            MailRecipientType.TO: "To",
            MailRecipientType.CC: "Cc",
            MailRecipientType.BCC: "Bcc",
        }
        by_type: dict[MailRecipientType, list[MailRecipientCreate]] = {t: [] for t in header_map}
        for r in payload.recipients:
            if r.recipient_type in by_type:
                by_type[r.recipient_type].append(r)

        for rtype, header in header_map.items():
            if by_type[rtype]:
                mime[header] = _fmt(by_type[rtype])

        if payload.reply_to:
            mime["Reply-To"] = payload.reply_to

        alt = MIMEMultipart("alternative")
        if payload.content.body_text:
            alt.attach(MIMEText(payload.content.body_text, "plain", "utf-8"))
        if payload.content.body_html:
            alt.attach(MIMEText(payload.content.body_html, "html", "utf-8"))
        mime.attach(alt)
        return mime

    @staticmethod
    def decorate_reply(
        mime: MIMEMultipart,
        original: MailMessage,
        *,
        reply_all: bool,
    ) -> None:
        """Add In-Reply-To, References, Re: prefix, and optionally CC."""
        if original.external_message_id:
            mime["In-Reply-To"] = original.external_message_id
            mime["References"] = original.external_message_id
        if not (original.subject or "").lower().startswith("re:"):
            mime.replace_header("Subject", f"Re: {original.subject or ''}")
        if reply_all:
            _MimeBuilder._add_reply_all_cc(mime, original)

    @staticmethod
    def decorate_forward(mime: MIMEMultipart, original: MailMessage) -> None:
        """Ensure the subject carries a Fwd: prefix."""
        orig_subject = original.subject or ""
        if not orig_subject.lower().startswith("fwd:"):
            mime.replace_header("Subject", f"Fwd: {orig_subject}")

    @staticmethod
    def _add_reply_all_cc(mime: MIMEMultipart, original: MailMessage) -> None:
        our_email = app_settings.MAIL_EMAIL.lower()
        extra = [
            r.email_address
            for r in original.recipients
            if r.email_address.lower() != our_email and r.recipient_type in (MailRecipientType.TO, MailRecipientType.CC)
        ]
        if not extra:
            return
        existing = mime.get("Cc", "")
        all_cc = ", ".join(filter(None, [existing, *extra]))
        if mime.get("Cc"):
            mime.replace_header("Cc", all_cc)
        else:
            mime["Cc"] = all_cc

    @staticmethod
    def build_forward_payload(
        payload: MailMessageCreate,
        original: MailMessage,
    ) -> MailMessageCreate:
        """Inject forward header and quoted body into a MailMessageCreate."""
        orig_subject = original.subject or ""
        fwd_subject = orig_subject if orig_subject.lower().startswith("fwd:") else f"Fwd: {orig_subject}"
        orig_text = (original.content.body_text if original.content else "") or ""
        orig_html = (original.content.body_html if original.content else "") or ""

        sep_text = (
            f"\n---------- Пересланное письмо ----------\n"
            f"От: {original.sender_name} <{original.sender_email}>\n"
            f"Дата: {original.sent_at}\n"
            f"Тема: {orig_subject}\n"
        )
        sep_html = (
            f"<br><br><hr>"
            f"<p><b>Пересланное письмо</b><br>"
            f"От: {original.sender_name} &lt;{original.sender_email}&gt;<br>"
            f"Дата: {original.sent_at}<br>"
            f"Тема: {orig_subject}</p>"
        )
        return payload.model_copy(
            update={
                "subject": fwd_subject,
                "content": MailContentCreate(
                    body_text=f"{payload.content.body_text or ''}{sep_text}{orig_text}",
                    body_html=f"{payload.content.body_html or ''}{sep_html}{orig_html}",
                ),
            }
        )


class _AttachmentHelper:
    """Uploads files to S3 and streams them into a MIMEMultipart."""

    def __init__(self, company_id: uuid.UUID) -> None:
        self._company_id = company_id

    @staticmethod
    def validate(files: list[UploadFile], max_total_size: int, max_count: int) -> None:
        total = sum(f.size for f in files if f.size is not None)
        if total > max_total_size:
            raise HTTPException(
                status_code=413,
                detail=(f"Суммарный размер вложений ({total / 1024 / 1024:.1f} МБ) превышает лимит {max_total_size / 1024 / 1024:.0f} МБ."),
            )

    @staticmethod
    def split_by_limit(
        files: list[UploadFile],
        max_total_size: int,
    ) -> tuple[list[UploadFile], list[UploadFile]]:
        """
        Жадно набирает файлы в SMTP-пачку пока не превысим лимит.
        Сортирует по размеру (меньшие сначала) чтобы максимизировать
        количество файлов, идущих по SMTP.

        Возвращает (smtp_files, oversized_files).
        Файлы без известного размера всегда идут в oversized.
        """
        known: list[UploadFile] = []
        unknown: list[UploadFile] = []

        for f in files:
            if f.size:
                known.append(f)
            else:
                unknown.append(f)

        known.sort(key=lambda f: f.size or 0)

        smtp_files: list[UploadFile] = []
        oversized_files: list[UploadFile] = []
        accumulated = 0

        for f in known:
            size = f.size or 0
            if accumulated + size <= max_total_size:
                smtp_files.append(f)
                accumulated += size
            else:
                oversized_files.append(f)

        oversized_files.extend(unknown)

        return smtp_files, oversized_files

    async def upload(
        self,
        message_id: uuid.UUID,
        files: list[UploadFile],
        max_total_size: int,
    ) -> list[MailAttachment]:
        """Stream files to S3 sequentially, tracking running total size."""
        records: list[MailAttachment] = []
        uploaded_total = 0

        for upload in files:
            if not upload.filename:
                continue
            ext = "." + upload.filename.rsplit(".", 1)[-1].lower() if "." in upload.filename else ""
            object_key = f"mail/attachments/{self._company_id}/{uuid.uuid4()}{ext}"
            content_type = upload.content_type or "application/octet-stream"

            file_size = await s3_storage.upload_file_multipart(
                upload=upload,
                object_key=object_key,
                content_type=content_type,
            )
            uploaded_total += file_size

            if uploaded_total > max_total_size:
                await self.rollback_keys([r.s3_key for r in records] + [object_key])
                raise HTTPException(
                    status_code=413,
                    detail=(f"Суммарный размер вложений превышает лимит {max_total_size / 1024 / 1024:.0f} МБ."),
                )

            records.append(
                MailAttachment(
                    id=uuid.uuid4(),
                    company_id=self._company_id,
                    mail_message_id=message_id,
                    s3_key=object_key,
                    s3_bucket=app_settings.S3_BUCKET_NAME,
                    filename=upload.filename,
                    content_type=content_type,
                    file_size=file_size,
                )
            )

        return records

    @staticmethod
    async def attach_to_mime(
        mime: MIMEMultipart,
        files: list[UploadFile],
        chunk_size: int = 5 * 1024 * 1024,
    ) -> None:
        for upload in files:
            if not upload.filename:
                continue
            await upload.seek(0)
            chunks: list[bytes] = []
            while chunk := await upload.read(chunk_size):
                chunks.append(chunk)
            part = MIMEBase("application", "octet-stream")
            part.set_payload(b"".join(chunks))
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=upload.filename)
            mime.attach(part)

    @staticmethod
    async def attach_from_s3(mime: MIMEMultipart, original: MailMessage) -> None:
        """Fetch the original message's S3 attachments and attach them to MIME."""
        for att in original.attachments:
            try:
                file_bytes = await s3_storage.get_file_content(att.s3_key)
                part = MIMEBase("application", "octet-stream")
                part.set_payload(file_bytes)
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", "attachment", filename=att.filename)
                mime.attach(part)
            except Exception as exc:
                log.warning("Forward: cannot fetch S3 attachment %s: %s", att.s3_key, exc)

    @staticmethod
    async def rollback_keys(keys: list[str]) -> None:
        for key in keys:
            try:
                await s3_storage.delete_file(key)
            except Exception as exc:
                log.warning("Cannot delete S3 file %s during rollback: %s", key, exc)

    @staticmethod
    def suggest_removals(
        files: list[UploadFile],
        max_total_bytes: int,
    ) -> tuple[list[str], float]:
        """
        Greedily drop the heaviest files until total fits within the limit.
        Returns (filenames_to_remove, suggested_limit_mb).
        Suggested limit = 75 % of cap (SMTP base64 adds ~33 % overhead).
        """
        named = sorted(
            ((f.filename or "unknown", f.size or 0) for f in files if f.filename),
            key=lambda x: x[1],
            reverse=True,
        )
        total = sum(size for _, size in named)
        to_remove: list[str] = []
        for name, size in named:
            if total <= max_total_bytes:
                break
            to_remove.append(name)
            total -= size
        return to_remove, round(max_total_bytes / 1024 / 1024 * 0.75, 1)


_SMTP_SIZE_CODES: frozenset[int] = frozenset({523, 552, 554})
_SMTP_SIZE_PATTERNS: tuple[str, ...] = (
    r"size",
    r"too large",
    r"message too big",
    r"exceeds.*limit",
    r"limit.*exceed",
    r"552",
    r"554",
    r"523",
)


class _SmtpHelper:
    """Sends MIME messages and classifies SMTP errors. Stateless."""

    @staticmethod
    async def send(mime: MIMEMultipart) -> None:
        sender = f"<{app_settings.MAIL_EMAIL}>"
        recipients = [f"<{addr}>" for header in ("To", "Cc", "Bcc") if header in mime for _, addr in getaddresses([mime[header]])]
        await aiosmtplib.send(
            mime,
            hostname=app_settings.MAIL_SMTP_HOST,
            port=app_settings.MAIL_SMTP_PORT,
            username=app_settings.MAIL_EMAIL,
            password=app_settings.MAIL_PASSWORD,
            use_tls=False,
            start_tls=True,
            sender=sender,
            recipients=recipients,
            timeout=30,
        )

    @staticmethod
    def is_size_error(exc: Exception) -> bool:
        if getattr(exc, "code", None) in _SMTP_SIZE_CODES:
            return True
        return any(re.search(p, str(exc).lower()) for p in _SMTP_SIZE_PATTERNS)
