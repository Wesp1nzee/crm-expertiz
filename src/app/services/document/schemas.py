import uuid
from datetime import datetime
from enum import Enum
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator


class EntryType(str, Enum):
    FOLDER = "folder"
    FILE = "file"


class FolderBase(BaseModel):
    name: str
    parent_id: uuid.UUID | None = None


class FolderCreate(FolderBase):
    pass


class FolderResponse(FolderBase):
    id: uuid.UUID
    created_by_id: uuid.UUID | None
    creator_name: str | None = None  # ← Добавили имя создателя
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class DocumentResponse(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID | None
    folder_id: uuid.UUID | None
    title: str
    file_size: int
    file_extension: str
    uploaded_by_id: uuid.UUID | None
    uploaded_by_name: str | None = None  # ← Добавили имя загрузившего
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


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
