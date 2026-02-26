from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.auth.deps import UserContext, get_current_user
from src.app.core.auth.session import SessionManager
from src.app.core.database.session import get_db
from src.app.core.redis import get_redis_client
from src.app.services.user.models import User
from src.app.services.user.schemas import LogoutResponse, SearchResultDTO, UserCreate, UserFilterParams, UserLoginSchema, UserRead, UserUpdate
from src.app.services.user.service import UserService

router = APIRouter(prefix="/api/users", tags=["Users & Auth"])


@router.get("/suggest", response_model=list[SearchResultDTO])
async def suggest_users(
    q: str = Query(..., min_length=1), db: AsyncSession = Depends(get_db), current_user: UserContext = Depends(get_current_user)
) -> list[SearchResultDTO]:
    service = UserService(db)
    results = await service.search_name(q, current_user.company_id)
    return [SearchResultDTO(id=r.id, name=r.full_name) for r in results]


@router.post("/login", response_model=UserRead)
async def login(credentials: UserLoginSchema, request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> User:
    user_service = UserService(db)
    user = await user_service.authenticate(credentials)

    if not user or not user.is_active or not user.company.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверные учетные данные или доступ запрещен")

    redis_client = await get_redis_client()
    session_manager = SessionManager(redis_client)

    session_id = request.cookies.get("session_id")

    redis_session = None
    if session_id:
        redis_session = await session_manager.get_session(session_id)

    if session_id and redis_session:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Вы уже авторизованы")

    elif session_id and not redis_session:
        response.delete_cookie("session_id")

    new_session_id = await session_manager.create_session(user=user)
    response.set_cookie(key="session_id", value=new_session_id, httponly=True, secure=True, samesite="lax", max_age=604_800)  # Куки на 1 неделю
    await user_service.set_online_status(user.id, True)
    return user


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request, response: Response, db: AsyncSession = Depends(get_db), current_user: UserContext = Depends(get_current_user)
) -> LogoutResponse:
    session_id = request.cookies.get("session_id")

    if session_id:
        redis_client = await get_redis_client()
        session_manager = SessionManager(redis_client)
        await session_manager.delete_session(session_id)

    response.delete_cookie("session_id")

    user_service = UserService(db)
    await user_service.set_online_status(current_user.id, False)

    return LogoutResponse()


# TODO: Исправить типы response /docs
@router.get("/me", response_model=UserRead)
async def get_me(current_user: UserContext = Depends(get_current_user)) -> UserContext:
    return current_user


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(user_in: UserCreate, db: AsyncSession = Depends(get_db), current_user: UserContext = Depends(get_current_user)) -> User:
    user_service = UserService(db)
    return await user_service.create_user(creator=current_user, user_in=user_in)


@router.get("", response_model=list[UserRead])
async def list_users(
    params: UserFilterParams = Depends(), db: AsyncSession = Depends(get_db), current_user: UserContext = Depends(get_current_user)
) -> list[dict[Any, Any]]:
    user_service = UserService(db)
    return await user_service.get_users_list(current_user, params)


@router.delete("/{user_id}")
async def delete_user(user_id: UUID, db: AsyncSession = Depends(get_db), current_user: UserContext = Depends(get_current_user)) -> None:
    service = UserService(db)
    await service.delete_user(user_id)


@router.patch("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def udate_user(
    user_id: UUID, case_data: UserUpdate, db: AsyncSession = Depends(get_db), current_user: UserContext = Depends(get_current_user)
) -> None:
    service = UserService(db)
    await service.update_user(user_id, case_data, current_user.role)
