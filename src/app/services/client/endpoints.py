import uuid
from collections.abc import Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.auth.deps import get_current_user
from src.app.core.database import get_db
from src.app.services.client.schemas import ClientCreate, ClientFilters, ClientFullResponse, ClientListResponse, ClientUpdate, SearchResultDTO
from src.app.services.client.service import ClientService
from src.app.services.user.models import User, UserRole

router = APIRouter(prefix="/api/clients", tags=["Clients"])


@router.get("/suggest", response_model=Sequence[SearchResultDTO])
async def suggest_clients(
    q: str = Query(..., min_length=2), db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> Sequence[SearchResultDTO]:
    service = ClientService(db)
    results = await service.search_name(q, current_user.company_id)

    return [SearchResultDTO(id=r[0], name=r[1]) for r in results]


@router.post(
    "",
    response_model=ClientFullResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать нового клиента",
    description="Создает клиента и опционально первый контакт",
)
async def create_client(
    client_data: ClientCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> ClientFullResponse:
    if current_user.role == UserRole.EXPERT:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет прав для создания клиентов")

    service = ClientService(db)
    try:
        return await service.create_client(client_data, current_user.company_id, current_user.role)
    except IntegrityError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Клиент с таким ИНН уже существует или нарушены требования",
        ) from err
    except Exception as err:
        print(err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Внутренняя ошибка сервера при создании клиента",
        ) from err


@router.get(
    "",
    response_model=ClientListResponse,
    summary="Получить список клиентов",
    description="Возвращает список клиентов с пагинацией и фильтрацией",
)
async def get_clients(
    type: str | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ClientListResponse:
    service = ClientService(db)
    filters = ClientFilters(type=type, search=search, page=page, limit=limit)
    try:
        return await service.get_clients(filters, current_user.company_id)
    except Exception as err:
        print(err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Не удалось получить список клиентов",
        ) from err


@router.get(
    "/{client_id}",
    response_model=ClientFullResponse,
    summary="Получить клиента по ID",
    description="Возвращает полную карточку клиента со всеми контактами",
)
async def get_client(
    client_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> ClientFullResponse:
    service = ClientService(db)
    client = await service.get_client_by_id(str(client_id), current_user.company_id, current_user.role)

    if client is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Клиент с ID {client_id} не найден",
        )

    return client


@router.patch(
    "/{client_id}",
    response_model=ClientFullResponse,
    summary="Обновить данные клиента",
    description="Частичное обновление полей клиента",
)
async def update_client(
    client_id: uuid.UUID, update_data: ClientUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> ClientFullResponse:
    if current_user.role == UserRole.EXPERT:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет прав для обновления данных клиента")

    service = ClientService(db)
    try:
        updated_client = await service.update_client(str(client_id), update_data, current_user.company_id, current_user.role)
        if not updated_client:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Клиент с ID {client_id} не найден",
            )
        return updated_client
    except IntegrityError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ошибка валидации при обновлении (возможно, ИНН уже занят)",
        ) from err


@router.delete(
    "/{client_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить клиента",
    description="Полное удаление клиента и всех его контактов",
)
async def delete_client(client_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)) -> None:
    if current_user.role == UserRole.EXPERT:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет прав для удаления клиента")

    service = ClientService(db)
    success = await service.delete_client(str(client_id), current_user.company_id, current_user.role)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Клиент с ID {client_id} не найден",
        )
    return None
