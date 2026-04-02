from fastapi import APIRouter, Depends, HTTPException, status
from redis.asyncio import Redis

from src.app.core.redis import get_redis_client
from src.app.core.schemas.dadata import InnLookupResult
from src.app.services.dadata.service import dadata_manager

router = APIRouter(prefix="/api/dadata", tags=["Dadata"])


@router.get("/lookup/{inn}", response_model=InnLookupResult)
async def lookup_company_by_inn(
    inn: str,
    redis: Redis = Depends(get_redis_client),
) -> InnLookupResult:
    """
    Поиск организации по ИНН через сервис Dadata.

    Args:
        inn: ИНН организации (10 или 12 цифр).

    Returns:
        Данные организации из Dadata.

    Raises:
        HTTPException: Если организация не найдена или произошла ошибка API.
    """
    # Валидация ИНН (10 или 12 цифр)
    if not inn.isdigit() or len(inn) not in (10, 12):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ИНН должен содержать 10 или 12 цифр",
        )

    result = await dadata_manager.find_by_inn(inn, redis)

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Организация с данным ИНН не найдена",
        )

    return result
