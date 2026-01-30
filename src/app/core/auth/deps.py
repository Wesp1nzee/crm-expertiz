from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.auth.session import SessionManager
from src.app.core.database.session import get_db
from src.app.core.redis import get_redis_client
from src.app.services.company.models import Company
from src.app.services.user.models import User, UserRole


class UserSessionData(BaseModel):
    id: UUID
    email: str
    full_name: str
    role: str
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
    company: CompanySessionData


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User | None:
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Не авторизован")

    redis_client = await get_redis_client()
    session_manager = SessionManager(redis_client)

    session_data_raw = await session_manager.get_session(session_id)
    if not session_data_raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Сессия истекла или недействительна")

    try:
        cached_data = CachedSessionData.model_validate(session_data_raw)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Данные сессии некорректны") from None

    user_info = cached_data.user
    company_info = cached_data.company

    if not user_info.can_authenticate:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ваш аккаунт заблокирован")

    company_id_from_cache = company_info.id if company_info else None
    settings_from_cache = user_info.settings

    user = User(
        id=user_info.id,
        email=user_info.email,
        full_name=user_info.full_name,
        role=UserRole(user_info.role),
        is_active=user_info.is_active,
        can_authenticate=user_info.can_authenticate,
        specialization=user_info.specialization,
        company_id=company_id_from_cache,
        settings=settings_from_cache,
    )

    user.company = Company(
        id=company_info.id,
        name=company_info.name,
        is_active=company_info.is_active,
    )

    return user
