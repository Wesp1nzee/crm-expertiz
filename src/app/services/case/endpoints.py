import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.auth.deps import get_current_user
from src.app.core.database import get_db
from src.app.services.case.schemas import (
    CaseCreateRequest,
    CaseDetailsResponse,
    CaseResponse,
    CaseSuggestionResponse,
    CaseUpdateRequest,
    GetCasesQuery,
    GetCasesResponse,
)
from src.app.services.case.service import CaseService
from src.app.services.user.models import User, UserRole

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cases", tags=["Cases"])


@router.get(
    "/suggest",
    response_model=list[CaseSuggestionResponse],
    summary="Подсказки по делам",
    description="Возвращает подсказки по делам по поисковому запросу в полях number или case_number",
)
async def suggest_case(
    q: str = Query(..., min_length=1, description="Поисковый запрос (минимум 1 символ)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CaseSuggestionResponse]:
    service = CaseService(db)
    try:
        return await service.suggest_cases(q, current_user.id, current_user.role)
    except Exception as err:
        logger.exception("Error during case suggestion search")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при поиске дел",
        ) from err


@router.get(
    "",
    response_model=GetCasesResponse,
    summary="Получить список дел",
    description="Возвращает список дел с фильтрацией, пагинацией и статистикой",
)
async def get_cases(
    params: GetCasesQuery = Depends(), db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> GetCasesResponse:
    service = CaseService(db)
    try:
        return await service.get_cases(params, current_user.id, current_user.role)
    except Exception as err:
        logger.exception("Error fetching cases list")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Не удалось получить список дел",
        ) from err


@router.post(
    "",
    response_model=CaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать новое дело",
    description="Регистрирует новое дело в системе",
)
async def create_case(
    case_data: CaseCreateRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> CaseResponse:
    if current_user.role == UserRole.EXPERT:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет прав для создания дел")

    service = CaseService(db)
    try:
        return await service.create_case(case_data, current_user.id, current_user.role)
    except HTTPException:
        raise
    except IntegrityError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Произошла ошибка при сохранении данных",
        ) from err
    except Exception as err:
        logger.exception("Unexpected error during case creation")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Внутренняя ошибка сервера при создании дела",
        ) from err


@router.get(
    "/{case_id}",
    response_model=CaseDetailsResponse,
    summary="Детальная информация о деле",
    description="Возвращает полные данные дела, включая клиента, документы, события и назначенных экспертов.",
)
async def get_case_details(
    case_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CaseDetailsResponse:
    service = CaseService(db)
    case_details = await service.get_case_details(
        case_id=str(case_id),
        user_id=current_user.id,
        user_role=current_user.role,
    )

    if case_details is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Дело не найдено или нет доступа")

    return case_details


@router.patch(
    "/{case_id}",
    response_model=CaseResponse,
    summary="Обновить данные дела",
    description="Частичное обновление информации по существующему делу",
)
async def update_case(
    case_id: UUID, case_data: CaseUpdateRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> CaseResponse:
    service = CaseService(db)
    try:
        result = await service.update_case(str(case_id), case_data, current_user.id, current_user.role)
        if not result:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Дело не найдено")
        return result
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err
    except Exception as err:
        logger.exception(f"Error updating case {case_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при обновлении данных дела",
        ) from err


@router.delete(
    "/{case_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить дело",
    description="Выполняет мягкое удаление дела (пометка deleted_at)",
)
async def delete_case(case_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)) -> None:
    if current_user.role == UserRole.EXPERT:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет прав для удаления дела")

    service = CaseService(db)
    success = await service.soft_delete_case(str(case_id), current_user.id, current_user.role)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Дело не найдено")
    return None
