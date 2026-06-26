import uuid
from datetime import datetime
from enum import Enum
from typing import Literal, Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from src.app.core.schemas.base import PaginatedResponse, PaginationMeta


class EntryType(str, Enum):
    FOLDER = "folder"
    FILE = "file"


class FolderBase(BaseModel):
    name: str
    parent_id: uuid.UUID | None = None
    case_id: uuid.UUID | None = None


class FolderCreate(FolderBase):
    pass


class FolderResponse(FolderBase):
    id: uuid.UUID
    created_by_id: uuid.UUID | None
    creator_name: str | None = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class DocumentResponse(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID | None
    folder_id: uuid.UUID | None
    title: str
    file_size: int
    file_extension: str
    status: str
    uploaded_by_id: uuid.UUID | None
    uploaded_by_name: str | None = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ShareInfoBrief(BaseModel):
    recipient_count: int
    public_link_count: int


class FileSystemEntry(BaseModel):
    id: uuid.UUID
    name: str
    type: EntryType
    size: int | None = None
    extension: str | None = None
    created_at: datetime
    created_by_id: uuid.UUID | None
    created_by_name: str | None = None
    parent_id: uuid.UUID | None
    case_id: uuid.UUID | None = None
    case_number: str | None = None
    share_info: ShareInfoBrief | None = None


class DocumentDownloadUrl(BaseModel):
    download_url: str


class DocumentUpdate(BaseModel):
    title: str | None = None
    case_id: uuid.UUID | None = None
    folder_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_update(self) -> Self:
        if not any([self.title, self.case_id, self.folder_id]):
            raise ValueError("Хотя бы одно поле должно быть указано для обновления")
        return self


class FolderUpdate(BaseModel):
    name: str | None = None
    parent_id: uuid.UUID | None = None
    case_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_update(self) -> Self:
        if not any([self.name, self.parent_id, self.case_id]):
            raise ValueError("Хотя бы одно поле должно быть указано для обновления")
        return self


class AssetUpdate(BaseModel):
    asset_id: uuid.UUID
    asset_type: EntryType
    data: DocumentUpdate | FolderUpdate

    @model_validator(mode="after")
    def validate_data_type(self) -> Self:
        if self.asset_type == EntryType.FILE and not isinstance(self.data, DocumentUpdate):
            raise ValueError("Для типа FILE данные должны быть DocumentUpdate")
        if self.asset_type == EntryType.FOLDER and not isinstance(self.data, FolderUpdate):
            raise ValueError("Для типа FOLDER данные должны быть FolderUpdate")
        return self


class DocumentsBulkDeleteRequest(BaseModel):
    folder_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)
    document_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)


class BulkDownloadRequest(BaseModel):
    folder_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)
    document_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)


class TrashOperationResponse(BaseModel):
    message: str
    moved: dict[str, int]
    model_config = ConfigDict(from_attributes=True)


class RestoreOperationResponse(BaseModel):
    message: str
    restored: dict[str, int]
    model_config = ConfigDict(from_attributes=True)


class FolderListItem(BaseModel):
    id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None = None
    case_id: uuid.UUID | None = None
    case_number: str | None = None
    created_at: datetime
    created_by_id: uuid.UUID
    created_by_name: str | None = None
    is_case_root: bool = False
    children_count: int = 0
    model_config = ConfigDict(from_attributes=True)


class FolderListResponse(PaginatedResponse[FolderListItem]):
    meta: PaginationMeta


class DocumentsBulkMoveRequest(BaseModel):
    folder_ids: list[uuid.UUID] = []
    document_ids: list[uuid.UUID] = []
    target_folder_id: uuid.UUID | None = None


class DocumentUploadInitRequest(BaseModel):
    original_filename: str = Field(..., min_length=1, max_length=1024)
    content_type: str = Field(..., min_length=1, max_length=255)
    file_size: int = Field(..., ge=1, le=50 * 1024 * 1024 * 1024)  # Лимит до 50 ГБ
    case_id: uuid.UUID | None = None
    folder_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=1024)


class PresignedPostData(BaseModel):
    url: str
    fields: dict[str, str]


class MultipartInitData(BaseModel):
    upload_id: str
    key: str


class DocumentUploadInitResponse(BaseModel):
    document_id: uuid.UUID
    key: str
    strategy: Literal["presigned_post", "multipart"]
    presigned_post: PresignedPostData | None = None
    multipart: MultipartInitData | None = None


class MultipartLinksRequest(BaseModel):
    document_id: uuid.UUID
    key: str
    upload_id: str
    parts_count: int = Field(..., ge=1, le=10_000)


class PartUploadLink(BaseModel):
    part_number: int
    upload_url: str


class MultipartLinksResponse(BaseModel):
    document_id: uuid.UUID
    key: str
    upload_id: str
    parts: list[PartUploadLink]


class PartETag(BaseModel):
    """Данные одной загруженной части."""

    part_number: int = Field(..., ge=1, serialization_alias="PartNumber", validation_alias=AliasChoices("part_number", "PartNumber"))
    etag: str = Field(..., min_length=1, serialization_alias="ETag", validation_alias=AliasChoices("etag", "ETag"))

    model_config = ConfigDict(populate_by_name=True)


class MultipartCompleteRequest(BaseModel):
    document_id: uuid.UUID
    key: str
    upload_id: str
    parts: list[PartETag] = Field(..., min_length=1, max_length=10_000)


class UploadConfirmRequest(BaseModel):
    document_id: uuid.UUID
    key: str
