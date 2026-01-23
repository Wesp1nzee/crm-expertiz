from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.auth.session import SessionManager
from src.app.core.database.session import get_db
from src.app.core.redis import get_redis_client
from src.app.services.user.models import User


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Не авторизован")

    redis_client = await get_redis_client()
    session_manager = SessionManager(redis_client)

    session_data = await session_manager.get_session(session_id)
    if not session_data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Сессия истекла или недействительна")

    result = await db.execute(select(User).where(User.id == session_data["user_id"]))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Пользователь не найден")

    if not user.can_authenticate:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ваш аккаунт заблокирован")

    return user
