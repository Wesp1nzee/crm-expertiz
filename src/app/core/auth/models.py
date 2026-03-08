from typing import Any
from uuid import UUID

from pydantic import BaseModel

from src.app.services.user.models import UserRole


class UserSessionData(BaseModel):
    id: UUID
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    can_authenticate: bool
    specialization: str | None = None
    settings: dict[str, Any] = {}


class CompanySessionData(BaseModel):
    id: UUID
    name: str
    is_active: bool


class CachedSessionData(BaseModel):
    user_id: UUID
    user: UserSessionData
    company: CompanySessionData | None = None


class UserContext(BaseModel):
    id: UUID
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    can_authenticate: bool
    specialization: str | None = None
    company_id: UUID
    settings: dict[str, Any] = {}

    company: CompanySessionData | None = None

    model_config = {"from_attributes": True}
