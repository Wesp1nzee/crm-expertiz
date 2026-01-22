import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict


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
    parent_id: uuid.UUID | None


class DocumentDownloadUrl(BaseModel):
    download_url: str
