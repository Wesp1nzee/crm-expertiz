from fastapi import HTTPException, Request, Response, status
from pydantic import ValidationError

from src.app.core.auth.models import CachedSessionData, UserContext
from src.app.core.auth.session import SessionManager
from src.app.core.redis import get_redis_client


async def get_current_user(request: Request, response: Response) -> UserContext:
    """
    Получает текущего пользователя из сессии Redis и автоматически продлевает сессию.

    Returns:
        UserContext: Объект с данными пользователя и компании.

    Raises:
        HTTPException: 401 если не авторизован, 403 если доступ запрещен.
    """
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Пожалуйста, войдите в систему для доступа к этой странице")

    redis_client = await get_redis_client()
    session_manager = SessionManager(redis_client)

    session_data_raw = await session_manager.get_session(session_id)
    if not session_data_raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Время сессии истекло. Пожалуйста, войдите снова")

    current_ttl = await redis_client.ttl(f"session:{session_id}")

    if current_ttl < 518_400:  # Если осталось 6 дней, то продлеваем.
        await session_manager.refresh_session(session_id)
        response.set_cookie(
            key="session_id",
            value=session_id,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=604_800,  # 1 неделя
        )

    try:
        cached_data = CachedSessionData.model_validate(session_data_raw)
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Ошибка проверки данных") from e

    user_info = cached_data.user
    company_info = cached_data.company

    if not user_info.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступ к аккаунту временно ограничен")

    return UserContext(
        id=user_info.id,
        email=user_info.email,
        full_name=user_info.full_name,
        role=user_info.role,
        is_active=user_info.is_active,
        can_authenticate=user_info.can_authenticate,
        specialization=user_info.specialization,
        company_id=company_info.id if company_info else None,
        settings=user_info.settings,
        company=company_info,
    )
