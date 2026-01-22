import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentResponse(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID | None
    title: str
    original_filename: str
    file_size: int
    mime_type: str
    file_extension: str
    version: int
    created_at: datetime
    download_url: str | None = None

    model_config = ConfigDict(from_attributes=True)


class DocumentUpdate(BaseModel):
    title: str | None = None
    is_archived: bool | None = None


class DocumentDownloadUrl(BaseModel):
    download_url: str
