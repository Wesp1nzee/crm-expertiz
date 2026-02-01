from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from src.app.services.user.models import UserRole


class EmailConfigBase(BaseModel):
    smtp_host: str = Field(..., examples=["smtp.gmail.com"])
    smtp_port: int = Field(..., examples=[465])
    smtp_user: str = Field(..., examples=["ceo@company.com"])
    imap_host: str = Field(..., examples=["imap.gmail.com"])
    imap_port: int = Field(..., examples=[993])
    imap_user: str = Field(..., examples=["ceo@company.com"])


class EmailConfigCreate(EmailConfigBase):
    smtp_password: str = Field(..., min_length=1)
    imap_password: str = Field(..., min_length=1)


class EmailConfigRead(EmailConfigBase):
    last_sync_at: datetime | None = None
    sync_enabled: bool

    model_config = ConfigDict(from_attributes=True)


class UserBase(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=2, max_length=255)
    role: UserRole
    specialization: str | None = None


class UserCreate(UserBase):
    password: str = Field(..., min_length=12, description="Пароль должен быть не менее 8 символов")
    email_config: EmailConfigCreate | None = None
    is_active: bool = True
    settings: dict[str, Any] | None = Field(default_factory=dict)


class UserRead(UserBase):
    id: UUID
    is_active: bool
    can_authenticate: bool
    company_id: UUID
    settings: dict[str, Any]
    count_case: int = Field(0, description="Количество дел пользователя")

    model_config = ConfigDict(from_attributes=True)


class UserUpdate(BaseModel):
    full_name: str | None = None
    specialization: str | None = None
    can_authenticate: bool | None = None
    settings: dict[str, Any] | None = None


class UserLoginSchema(BaseModel):
    email: EmailStr
    password: str


class StatusResponse(BaseModel):
    status: str = "ok"
    message: str | None


class LogoutResponse(BaseModel):
    detail: str = "Successfully logged out"


class UserFilterParams(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None
    can_authenticate: bool | None = None
    search: str | None = Field(None, description="Поиск по имени или email")

    sort_by: str = Field("created_at", pattern="^(created_at|full_name|last_login|email)$")
    order: str = Field("desc", pattern="^(asc|desc)$")

    page: int = Field(1, ge=1)
    limit: int = Field(20, ge=1, le=100)


class SearchResultDTO(BaseModel):
    id: UUID
    name: str

    model_config = ConfigDict(from_attributes=True)


ROLE_PERMISSIONS = {
    UserRole.ADMIN: [UserRole.ADMIN, UserRole.CEO, UserRole.ACCOUNTANT, UserRole.EXPERT],
    UserRole.CEO: [UserRole.ACCOUNTANT, UserRole.EXPERT],
    UserRole.ACCOUNTANT: [UserRole.EXPERT],
    UserRole.EXPERT: [],
}
