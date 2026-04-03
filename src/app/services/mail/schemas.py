import uuid
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field, field_validator, model_validator

from src.app.services.mail.models import (
    MailFolder,
    MailMessageStatus,
    MailMessageType,
    MailRecipientType,
)

EmailField = Annotated[EmailStr, Field(description="Valid e-mail address")]
SubjectField = Annotated[str | None, Field(default=None, max_length=998, description="RFC 5322 subject length limit")]


class _Base(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        str_strip_whitespace=True,
        use_enum_values=True,
    )


class MailRecipientBase(_Base):
    email_address: EmailField
    recipient_type: MailRecipientType
    name: str | None = Field(default=None, max_length=255)


class MailAttachmentType(str, Enum):
    INCOMING = "incoming"
    OUTGOING = "outgoing"
    ALL = "all"


class MailAttachmentQuery(_Base):
    search: str | None = Field(description="Поиск по названию")
    sort_by: str = Field("created_at", description="Поле сортировки: name, created_at, size")
    order: str = Field("desc", pattern="^(asc|desc)$")
    page: int = Field(1, ge=1)
    limit: int = Field(25, ge=1, le=100)
    mail_attachment_type: MailAttachmentType


class MailThreadListItem(BaseModel):
    type: Literal["thread"] = "thread"
    id: uuid.UUID
    subject: str | None
    last_message_at: datetime | None
    message_count: int
    unread_count: int
    sender_name: str | None
    sender_email: str
    snippet: str | None
    is_starred: bool
    is_important: bool
    has_attachments: bool


class MailSingleMessageListItem(BaseModel):
    type: Literal["message"] = "message"
    id: uuid.UUID
    message_id: uuid.UUID
    subject: str | None
    last_message_at: datetime | None
    unread_count: int
    sender_name: str | None
    sender_email: str
    snippet: str | None
    is_starred: bool
    is_important: bool
    has_attachments: bool


MailListItem = Annotated[
    MailSingleMessageListItem | MailThreadListItem,
    Field(discriminator="type"),
]


class MailAttachmentResponse(_Base):
    name: str
    content_type: str
    created_at: datetime
    mail_message_id: uuid.UUID | None = None
    size: int


class MailAttachmentListItem(_Base):
    """Элемент списка вложений для отображения в списке."""

    id: uuid.UUID
    filename: str
    content_type: str
    file_size: int
    created_at: datetime
    thread_id: uuid.UUID
    message_subject: str | None
    message_sender_email: str
    message_sender_name: str | None
    message_type: MailMessageType
    folder: MailFolder


class MailRecipientCreate(MailRecipientBase):
    pass


class MailRecipientRead(MailRecipientBase):
    id: uuid.UUID


class MailAttachmentBase(_Base):
    filename: str = Field(max_length=500)
    content_type: str = Field(max_length=100)
    file_size: int = Field(ge=0, description="File size in bytes")


class MailAttachmentRead(MailAttachmentBase):
    id: uuid.UUID
    message_id: uuid.UUID = Field(alias="mail_message_id")
    attachment_id: str | None
    s3_key: str
    s3_bucket: str
    created_at: datetime

    @property
    @computed_field
    def stored_path(self) -> str:
        return f"{self.s3_bucket}/{self.s3_key}"


class MailSyncResult(_Base):
    folder: str
    fetched: int
    skipped: int
    errors: int
    synced_at: datetime


class MailAttachmentShort(MailAttachmentBase):
    id: uuid.UUID
    attachment_id: str | None = None


class MailContentBase(_Base):
    body_text: str | None = Field(default=None, description="Plain-text body")
    body_html: str | None = Field(default=None, description="HTML body")

    @model_validator(mode="after")
    def at_least_one_body(self) -> MailContentBase:
        if self.body_text is None and self.body_html is None:
            raise ValueError("At least one of body_text or body_html must be provided")
        return self


class MailContentCreate(MailContentBase):
    pass


class MailContentRead(MailContentBase):
    message_id: uuid.UUID


class MailMessageFilters(_Base):
    folder: MailFolder | None = None
    is_read: bool | None = None
    is_starred: bool | None = None
    is_important: bool | None = None
    is_spam: bool | None = None
    is_archived: bool | None = None
    case_id: uuid.UUID | None = None
    search: str | None = Field(default=None, max_length=255)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)


class MailMessageCreate(_Base):
    case_id: uuid.UUID | None = None
    sender_email: EmailField
    sender_name: str | None = Field(default=None, max_length=255)
    reply_to: str | None = Field(default=None, max_length=255)
    subject: SubjectField
    recipients: list[MailRecipientCreate] = Field(min_length=1)
    content: MailContentCreate

    frontend_domain: str | None = Field(
        description="Домен фронтенда, с которого отправлено сообщение", max_length=255, examples=["http://127.0.0.1:8000 "]
    )

    @field_validator("recipients")
    @classmethod
    def must_have_to_recipient(cls, v: list[MailRecipientCreate]) -> list[MailRecipientCreate]:
        if not any(r.recipient_type == MailRecipientType.TO for r in v):
            raise ValueError("At least one recipient with type 'to' is required")
        return v


class MailMessageUpdate(_Base):
    is_read: bool | None = None
    is_starred: bool | None = None
    is_important: bool | None = None
    is_spam: bool | None = None
    is_archived: bool | None = None
    is_deleted: bool | None = None
    case_id: uuid.UUID | None = None
    folder: MailFolder | None = None


class MailMessageBase(_Base):
    id: uuid.UUID
    external_message_id: str | None
    thread_id: uuid.UUID | None
    parent_id: uuid.UUID | None
    user_id: uuid.UUID
    case_id: uuid.UUID | None
    sender_email: str
    sender_name: str | None
    reply_to: str | None
    subject: str | None
    folder: MailFolder
    message_type: MailMessageType
    status: MailMessageStatus
    is_read: bool
    is_important: bool
    is_starred: bool
    is_spam: bool
    is_archived: bool
    is_deleted: bool
    size_bytes: int | None
    imap_uid: int | None
    sent_at: datetime | None
    processed_at: datetime
    updated_at: datetime


class MailMessageListItem(MailMessageBase):
    attachment_count: int = Field(default=0)
    has_attachments: bool = Field(default=False)
    recipients: list[MailRecipientRead]


class MailMessageRead(MailMessageBase):
    content: MailContentRead | None
    recipients: list[MailRecipientRead]
    attachments: list[MailAttachmentShort]


class MailThreadRead(_Base):
    thread_id: uuid.UUID
    subject: str | None
    message_count: int
    unread_count: int
    last_message_at: datetime
    participants: list[str]
    messages: list[MailMessageRead]


class MailMessageBulkAction(_Base):
    message_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    action: str

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        allowed = {"read", "unread", "star", "unstar", "archive", "unarchive", "delete", "restore", "spam", "not_spam"}
        if v not in allowed:
            raise ValueError(f"action must be one of {sorted(allowed)}")
        return v


class MailMessageBulkResult(_Base):
    updated: int
    failed: list[uuid.UUID] = Field(default_factory=list)


class MailSendErrorCode(str):
    """
    Коды ошибок отправки — используются фронтендом для отображения
    конкретного сообщения пользователю.
    """

    # SMTP отклонил письмо из-за превышения размера
    SMTP_SIZE_EXCEEDED = "SMTP_SIZE_EXCEEDED"
    # SMTP недоступен / таймаут / авторизация
    SMTP_CONNECTION_ERROR = "SMTP_CONNECTION_ERROR"
    # Получатель не найден / отклонён SMTP
    SMTP_RECIPIENT_REJECTED = "SMTP_RECIPIENT_REJECTED"
    # Неизвестная ошибка SMTP
    SMTP_ERROR = "SMTP_ERROR"
    # Ошибка загрузки вложений в S3
    ATTACHMENT_UPLOAD_ERROR = "ATTACHMENT_UPLOAD_ERROR"


class MailSendResult(_Base):
    """
    Результат отправки письма.
    """

    message_id: uuid.UUID | None = None
    status: MailMessageStatus
    external_message_id: str | None = None
    sent_at: datetime | None = None

    message_saved: bool = True

    error_code: str | None = None
    error: str | None = None

    rejected_files: list[str] = Field(default_factory=list)
    oversized_links: bool = False


class PaginatedMailMessages(_Base):
    items: list[MailMessageListItem]
    total: int
    page: int
    page_size: int
    has_next: bool

    @model_validator(mode="after")
    def compute_has_next(self) -> PaginatedMailMessages:
        self.has_next = (self.page * self.page_size) < self.total
        return self


class MailThreadMeta(_Base):
    thread_id: uuid.UUID
    subject: str | None
    message_count: int
    unread_count: int
    last_message_at: datetime | None
    participants: list[str]

    model_config = {"from_attributes": True}


class PaginatedMailThread(_Base):
    meta: MailThreadMeta
    items: list[MailMessageRead]
    total: int
    page: int
    page_size: int
    has_next: bool


class OversizedFileOut(_Base):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    file_size: int


class OversizedBatchOut(_Base):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    share_token: str
    created_at: datetime
    files: list[OversizedFileOut]


class OversizedFileDownloadOut(_Base):
    file_id: uuid.UUID
    filename: str
    url: str
    expires_in: int


class OversizedZipOut(_Base):
    files: list[OversizedFileDownloadOut]


class EmailContactRead(_Base):
    email: str
    name: str | None = None
    usage_count: int = 0


class EmailContactAutocompleteResponse(_Base):
    items: list[EmailContactRead]
    total: int


class LinkMailToCaseRequest(_Base):
    """Request schema for linking a mail message to a case."""

    case_id: uuid.UUID


class LinkMailToCaseResponse(_Base):
    """Response schema for linking a mail message to a case."""

    message_id: uuid.UUID
    case_id: uuid.UUID
    success: bool
