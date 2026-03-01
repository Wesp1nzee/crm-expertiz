import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from src.app.services.document.models import PermissionLevel, ShareType


class ShareResourceSchema(BaseModel):
    """Один ресурс (документ или папка) внутри батча."""

    document_id: uuid.UUID | None = None
    folder_id: uuid.UUID | None = None
    permission_level: PermissionLevel = PermissionLevel.VIEW
    can_download: bool = True

    @model_validator(mode="after")
    def validate_exactly_one_resource(self) -> ShareResourceSchema:
        has_doc = self.document_id is not None
        has_folder = self.folder_id is not None
        if has_doc == has_folder:
            raise ValueError("Укажите ровно один из параметров: document_id или folder_id")
        return self


class CreateLinkShareSchema(BaseModel):
    """Создание публичной ссылки (ShareType.LINK)."""

    resources: list[ShareResourceSchema] = Field(..., min_length=1)
    password: str | None = Field(None, description="Пароль для доступа по ссылке (опционально)")
    expires_at: datetime | None = None
    message: str | None = None


class CreateUserShareSchema(BaseModel):
    """
    Передача файлов/папок одному или нескольким сотрудникам (ShareType.USER).

    Для каждого получателя создаётся отдельный ShareBatch — это позволяет
    независимо управлять доступом: отзывать у одного, не затрагивая других.
    """

    shared_with_user_ids: list[uuid.UUID] = Field(
        ...,
        min_length=1,
        description="Список ID сотрудников-получателей (можно передать сразу нескольким)",
    )
    resources: list[ShareResourceSchema] = Field(..., min_length=1)
    expires_at: datetime | None = None
    message: str | None = None


class AccessLinkSchema(BaseModel):
    """Запрос доступа к публичной ссылке."""

    token: str
    password: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None


class DocumentShareOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID | None
    folder_id: uuid.UUID | None
    permission_level: PermissionLevel
    can_download: bool

    model_config = {"from_attributes": True}


class ShareRecipientOut(BaseModel):
    """Краткая информация о получателе шара — для отображения в карточке файла."""

    batch_id: uuid.UUID
    user_id: uuid.UUID
    full_name: str
    email: str
    permission_level: PermissionLevel
    can_download: bool
    expires_at: datetime | None
    is_active: bool
    shared_at: datetime

    model_config = {"from_attributes": True}


class ShareBatchOut(BaseModel):
    id: uuid.UUID
    share_type: ShareType
    share_token: str | None
    expires_at: datetime | None
    is_active: bool
    created_at: datetime
    message: str | None
    shares: list[DocumentShareOut]

    # USER-шаринг
    shared_with_user_id: uuid.UUID | None = None

    # LINK-шаринг
    has_password: bool = False
    current_views: int = 0
    current_downloads: int = 0

    model_config = {"from_attributes": True}


class ResourceShareInfoOut(BaseModel):
    """
    Сводка о том, кому передан конкретный документ или папка.
    Возвращается при клике на файл/папку в интерфейсе.
    """

    resource_id: uuid.UUID
    resource_type: str  # "document" | "folder"
    recipients: list[ShareRecipientOut]
    public_links: list[ShareBatchOut]


class ShareAccessLinkResult(BaseModel):
    """Результат успешного доступа к публичной ссылке."""

    batch_id: uuid.UUID
    shares: list[DocumentShareOut]
    can_download: bool
