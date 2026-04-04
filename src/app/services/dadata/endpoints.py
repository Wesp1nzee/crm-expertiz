from fastapi import APIRouter, Depends, HTTPException, Query, status
from redis.asyncio import Redis

from src.app.core.redis import get_redis_client
from src.app.services.dadata.dadata import AddressLookupResult, CourtLookupResult, InnLookupResult, PartyLookupResult
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


@router.get("/suggest/address", response_model=AddressLookupResult)
async def suggest_address(
    query: str = Query(..., min_length=1, max_length=255, description="Строка поиска (часть адреса, индекс, город, улица)"),
    count: int = Query(10, ge=1, le=20, description="Количество результатов (1-20)"),
    from_bound: str | None = Query(None, description="Начальная граница поиска (например, 'city')"),
    to_bound: str | None = Query(None, description="Конечная граница поиска (например, 'street')"),
    redis: Redis = Depends(get_redis_client),
) -> AddressLookupResult:
    """
    Поиск адресов по подсказкам через сервис Dadata.

    Args:
        query: Строка поиска (часть адреса, индекс, название города/улицы).
        count: Количество результатов (по умолчанию 10, максимум 20).
        from_bound: Начальная граница поиска (например, "city").
        to_bound: Конечная граница поиска (например, "street").

    Returns:
        Список предложенных адресов из Dadata.

    Raises:
        HTTPException: Если произошла ошибка API.
    """
    result = await dadata_manager.suggest_address(
        query=query,
        redis=redis,
        count=count,
        from_bound=from_bound,
        to_bound=to_bound,
    )

    return result


@router.get("/suggest/court", response_model=CourtLookupResult)
async def suggest_court(
    query: str = Query(..., min_length=1, max_length=255, description="Строка поиска (название, адрес суда)"),
    count: int = Query(10, ge=1, le=20, description="Количество результатов (1-20)"),
    redis: Redis = Depends(get_redis_client),
) -> CourtLookupResult:
    """
    Поиск судов по подсказкам через сервис Dadata.

    Args:
        query: Строка поиска (название суда, адрес).
        count: Количество результатов (по умолчанию 10, максимум 20).

    Returns:
        Список предложенных судов из Dadata.

    Raises:
        HTTPException: Если произошла ошибка API.
    """
    result = await dadata_manager.suggest_court(
        query=query,
        redis=redis,
        count=count,
    )

    return result


@router.get("/suggest/party", response_model=PartyLookupResult)
async def suggest_party(
    query: str = Query(..., min_length=1, max_length=255, description="Строка поиска (ИНН, название, адрес)"),
    count: int = Query(10, ge=1, le=20, description="Количество результатов (1-20)"),
    party_type: str | None = Query(None, description="Тип организации (LEGAL/INDIVIDUAL)"),
    status: list[str] | None = Query(None, description="Фильтр по статусу (ACTIVE, LIQUIDATING, LIQUIDATED, BANKRUPT, REORGANIZING)"),
    okved: list[str] | None = Query(None, description="Фильтр по коду ОКВЭД"),
    redis: Redis = Depends(get_redis_client),
) -> PartyLookupResult:
    """
    Поиск организаций по подсказкам через сервис Dadata.

    Args:
        query: Строка поиска (ИНН, ИНН/КПП, ОГРН, название, ФИО, адрес).
        count: Количество результатов (по умолчанию 10, максимум 20).
        party_type: Тип организации (LEGAL — юрлицо, INDIVIDUAL — ИП).
        status: Фильтр по статусу организации.
        okved: Фильтр по коду ОКВЭД.

    Returns:
        Список предложенных организаций из Dadata.

    Raises:
        HTTPException: Если произошла ошибка API.
    """
    result = await dadata_manager.suggest_party(
        query=query,
        redis=redis,
        count=count,
        party_type=party_type,
        status=status,
        okved=okved,
    )

    return result
