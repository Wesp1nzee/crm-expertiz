import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.auth.deps import get_current_user
from src.app.core.auth.models import UserContext
from src.app.core.database.session import get_db
from src.app.core.schemas import PaginatedResponse
from src.app.services.client.schemas import (
    ClientCreate,
    ClientFullResponse,
    ClientShortResponse,
    ClientUpdate,
    ContactCreate,
    ContactResponse,
    ContactUpdate,
    SearchResultDTO,
)
from src.app.services.client.service import ClientService
from src.app.services.user.models import UserRole

router = APIRouter(prefix="/api/clients", tags=["Clients"])


@router.get("/suggest", response_model=list[SearchResultDTO])
async def suggest_clients(
    q: str = Query(..., min_length=2),
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> list[SearchResultDTO]:
    service = ClientService(db)
    results = await service.search_name(q, current_user.company_id)
    return [SearchResultDTO(id=r[0], name=r[1]) for r in results]


@router.post("", response_model=ClientFullResponse, status_code=status.HTTP_201_CREATED)
async def create_client(
    client_data: ClientCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> ClientFullResponse:
    if current_user.role == UserRole.EXPERT:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет прав для создания клиентов")

    service = ClientService(db)
    try:
        return await service.create_client(client_data, current_user.company_id, current_user.role)
    except IntegrityError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Клиент с таким ИНН уже существует в вашей компании",
        ) from err


@router.get("", response_model=PaginatedResponse[ClientShortResponse])
async def get_clients(
    request: Request,
    type: str | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> PaginatedResponse[ClientShortResponse]:
    service = ClientService(db)

    response = await service.get_clients(company_id=current_user.company_id, page=page, limit=limit, client_type=type, search=search)

    path = request.url.path

    def build_relative_link(p: int) -> str:
        params: dict[str, str] = dict(request.query_params)
        params.update({"page": str(p), "limit": str(limit)})
        return f"{path}?{urlencode(params)}"

    if response.meta.has_next:
        response.meta.next_page_url = build_relative_link(page + 1)
    if response.meta.has_prev:
        response.meta.prev_page_url = build_relative_link(page - 1)

    return response


@router.get("/{client_id}", response_model=ClientFullResponse)
async def get_client(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> ClientFullResponse:
    service = ClientService(db)
    client = await service.get_client_by_id(str(client_id), current_user.company_id, current_user.role)
    if not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Клиент не найден")
    return client


@router.patch("/{client_id}", response_model=ClientFullResponse)
async def update_client(
    client_id: uuid.UUID,
    update_data: ClientUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> ClientFullResponse:
    if current_user.role == UserRole.EXPERT:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет прав для обновления данных клиента")

    service = ClientService(db)
    updated_client = await service.update_client(str(client_id), update_data, current_user.company_id, current_user.role)
    if not updated_client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Клиент не найден")
    return updated_client


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_client(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> None:
    if current_user.role == UserRole.EXPERT:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет прав для удаления клиента")

    service = ClientService(db)
    if not await service.delete_client(str(client_id), current_user.company_id, current_user.role):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Клиент не найден")


@router.get("/{client_id}/contacts", response_model=list[ContactResponse])
async def get_client_contacts(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> list[ContactResponse]:
    """Получить все контакты клиента"""
    service = ClientService(db)
    contacts = await service.get_contacts(str(client_id), current_user.company_id)
    return [ContactResponse.model_validate(c) for c in contacts]


@router.post("/{client_id}/contacts", response_model=ContactResponse, status_code=status.HTTP_201_CREATED)
async def create_client_contact(
    client_id: uuid.UUID,
    contact_data: ContactCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> ContactResponse:
    """Создать контакт для клиента"""
    service = ClientService(db)
    contact = await service.create_contact(str(client_id), contact_data, current_user.company_id, current_user.role)
    return ContactResponse.model_validate(contact)


@router.patch("/contacts/{contact_id}", response_model=ContactResponse)
async def update_contact(
    contact_id: uuid.UUID,
    update_data: ContactUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> ContactResponse:
    """Обновить контакт"""
    service = ClientService(db)
    contact = await service.update_contact(str(contact_id), update_data, current_user.company_id, current_user.role)
    return ContactResponse.model_validate(contact)


@router.delete("/contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact(
    contact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> None:
    """Удалить контакт"""
    service = ClientService(db)
    if not await service.delete_contact(str(contact_id), current_user.company_id, current_user.role):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Контакт не найден")
