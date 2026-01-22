import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.services.client.models import Client, Contact
from src.app.services.client.schemas import (
    ClientCreate,
    ClientFilters,
    ClientFullResponse,
    ClientListResponse,
    ClientShortResponse,
    ClientUpdate,
)


class ClientService:
    def __init__(self, db_session: AsyncSession) -> None:
        self.db = db_session

    async def create_client(self, client_data: ClientCreate) -> ClientFullResponse:
        """
        Создает клиента.
        Если переданы данные initial_contact, сразу создает и привязывает контакт.
        """
        contact_data = client_data.initial_contact
        client_dict = client_data.model_dump(exclude={"initial_contact"})

        client = Client(**client_dict)
        self.db.add(client)

        if contact_data:
            contact = Contact(
                **contact_data.model_dump(),
                client=client,
            )
            self.db.add(contact)

        await self.db.commit()

        await self.db.refresh(client, attribute_names=["contacts"])

        return ClientFullResponse.model_validate(client)

    async def get_client_by_id(self, client_id: str) -> ClientFullResponse | None:
        """Получает полную информацию о клиенте с его контактами"""
        stmt = select(Client).options(selectinload(Client.contacts)).where(Client.id == uuid.UUID(client_id))
        result = await self.db.execute(stmt)
        client = result.scalars().first()

        if not client:
            return None

        return ClientFullResponse.model_validate(client)

    async def get_clients(self, filters: ClientFilters) -> ClientListResponse:
        """Получает список клиентов с фильтрацией и пагинацией"""
        stmt = select(Client)

        if filters.type:
            stmt = stmt.where(Client.type == filters.type)

        if filters.search:
            search_filter = or_(
                Client.name.ilike(f"%{filters.search}%"),
                Client.inn.ilike(f"%{filters.search}%"),
            )
            stmt = stmt.where(search_filter)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_count = (await self.db.execute(count_stmt)).scalar() or 0

        offset = (filters.page - 1) * filters.limit
        stmt = stmt.order_by(Client.created_at.desc()).offset(offset).limit(filters.limit)

        result = await self.db.execute(stmt)
        clients = result.scalars().all()

        total_pages = max(1, (total_count + filters.limit - 1) // filters.limit)

        return ClientListResponse(
            items=[ClientShortResponse.model_validate(c) for c in clients],
            total=total_count,
            page=filters.page,
            size=len(clients),
            pages=total_pages,
        )

    async def update_client(self, client_id: str, update_data: ClientUpdate) -> ClientFullResponse | None:
        """Обновляет данные клиента"""
        stmt = select(Client).where(Client.id == uuid.UUID(client_id))
        result = await self.db.execute(stmt)
        client = result.scalars().first()

        if not client:
            return None

        update_dict = update_data.model_dump(exclude_unset=True)
        for field, value in update_dict.items():
            setattr(client, field, value)

        await self.db.commit()
        return await self.get_client_by_id(client_id)

    async def delete_client(self, client_id: str) -> bool:
        """Удаляет клиента (каскадно удалятся и контакты из-за ondelete='CASCADE')"""
        stmt = select(Client).where(Client.id == uuid.UUID(client_id))
        result = await self.db.execute(stmt)
        client = result.scalars().first()

        if not client:
            return False

        await self.db.delete(client)
        await self.db.commit()
        return True
