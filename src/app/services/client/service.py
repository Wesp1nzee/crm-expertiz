import math
from collections.abc import Sequence
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.app.core.schemas import PaginatedResponse, PaginationMeta
from src.app.services.case.models import Case
from src.app.services.client.models import Client, Contact
from src.app.services.client.schemas import ClientCreate, ClientFullResponse, ClientShortResponse, ClientUpdate
from src.app.services.user.models import UserRole


class ClientService:
    def __init__(self, db_session: AsyncSession) -> None:
        self.db = db_session

    async def create_client(self, client_data: ClientCreate, company_id: UUID, user_role: UserRole) -> ClientFullResponse:
        """Создает клиента с привязкой к компании"""
        if user_role == UserRole.EXPERT:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Эксперт не может создавать новых клиентов",
            )

        contact_data = client_data.initial_contact
        client_dict = client_data.model_dump(exclude={"initial_contact"})

        client = Client(**client_dict, company_id=company_id)
        self.db.add(client)

        if contact_data:
            contact = Contact(
                **contact_data.model_dump(),
                client=client,
                company_id=company_id,
            )
            self.db.add(contact)

        await self.db.commit()
        await self.db.refresh(client, attribute_names=["contacts"])

        return ClientFullResponse.model_validate(client)

    async def get_client_by_id(self, client_id: str, company_id: UUID, user_role: UserRole) -> ClientFullResponse | None:
        """Получает полную информацию о клиенте (только для своей компании)"""
        stmt = select(Client).options(selectinload(Client.contacts)).where(Client.id == UUID(client_id), Client.company_id == company_id)
        result = await self.db.execute(stmt)
        client = result.scalars().first()

        if not client:
            return None

        return ClientFullResponse.model_validate(client)

    async def get_clients(
        self, company_id: UUID, page: int, limit: int, client_type: str | None = None, search: str | None = None
    ) -> PaginatedResponse[ClientShortResponse]:
        case_counts_subq = (
            select(
                Case.client_id,
                func.count(Case.id).label("total_cases"),
                func.sum(case((Case.status == "in_work", 1), else_=0)).label("active_cases"),
            )
            .where(Case.company_id == company_id)
            .group_by(Case.client_id)
            .subquery()
        )

        stmt = (
            select(Client, case_counts_subq.c.total_cases, case_counts_subq.c.active_cases)
            .outerjoin(case_counts_subq, Client.id == case_counts_subq.c.client_id)
            .where(Client.company_id == company_id)
        )

        if client_type:
            stmt = stmt.where(Client.type == client_type)
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(or_(Client.name.ilike(pattern), Client.inn.ilike(pattern)))

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_items = (await self.db.execute(count_stmt)).scalar() or 0

        offset = (page - 1) * limit
        stmt = stmt.order_by(Client.created_at.desc()).offset(offset).limit(limit)
        result = await self.db.execute(stmt)
        rows = result.all()

        items = []
        for row in rows:
            client_obj = row.Client
            client_obj.active_cases = row.active_cases or 0
            client_obj.total_cases = row.total_cases or 0
            items.append(ClientShortResponse.model_validate(client_obj))

        total_pages = math.ceil(total_items / limit) if total_items > 0 else 1

        meta = PaginationMeta(
            total_items=total_items, total_pages=total_pages, current_page=page, per_page=limit, has_next=page < total_pages, has_prev=page > 1
        )

        return PaginatedResponse[ClientShortResponse](items=items, meta=meta)

    async def update_client(self, client_id: str, update_data: ClientUpdate, company_id: UUID, user_role: UserRole) -> ClientFullResponse | None:
        """Обновляет данные клиента (с проверкой прав и компании)"""
        if user_role == UserRole.EXPERT:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Эксперт не может обновлять данные клиента",
            )

        stmt = select(Client).where(Client.id == UUID(client_id), Client.company_id == company_id)
        result = await self.db.execute(stmt)
        client = result.scalars().first()

        if not client:
            return None

        update_dict = update_data.model_dump(exclude_unset=True)
        for field, value in update_dict.items():
            setattr(client, field, value)

        await self.db.commit()
        return await self.get_client_by_id(str(client.id), company_id, user_role)

    async def delete_client(self, client_id: str, company_id: UUID, user_role: UserRole) -> bool:
        """Удаляет клиента (только для своей компании)"""
        if user_role == UserRole.EXPERT:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Эксперт не может удалять клиентов",
            )

        stmt = select(Client).where(Client.id == UUID(client_id), Client.company_id == company_id)
        result = await self.db.execute(stmt)
        client = result.scalars().first()

        if not client:
            return False

        await self.db.delete(client)
        await self.db.commit()
        return True

    async def search_name(self, query: str, company_id: UUID) -> Sequence[tuple[UUID, str]]:
        """Быстрый поиск по названию для выпадающих списков"""
        search_pattern = f"{query}%"
        stmt = (
            select(Client.id, Client.name)
            .where(
                Client.company_id == company_id,
                or_(
                    Client.name.ilike(search_pattern),
                    Client.short_name.ilike(search_pattern),
                ),
            )
            .limit(10)
        )
        result = await self.db.execute(stmt)
        return [(row.id, row.name) for row in result.all()]
