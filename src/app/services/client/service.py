from collections.abc import Sequence
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from src.app.services.case.models import Case
from src.app.services.client.models import Client, Contact
from src.app.services.client.schemas import (
    ClientCreate,
    ClientFilters,
    ClientFullResponse,
    ClientListResponse,
    ClientShortResponse,
    ClientUpdate,
)
from src.app.services.user.models import UserRole


class ClientService:
    def __init__(self, db_session: AsyncSession) -> None:
        self.db = db_session

    async def create_client(self, client_data: ClientCreate, company_id: UUID, user_role: UserRole) -> ClientFullResponse:
        """
        Создает клиента.
        Если переданы данные initial_contact, сразу создает и привязывает контакт.
        """
        # Только администраторы, CEO и бухгалтеры могут создавать клиентов
        if user_role == UserRole.EXPERT:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Эксперт не может создавать новых клиентов")

        contact_data = client_data.initial_contact
        client_dict = client_data.model_dump(exclude={"initial_contact"})

        client = Client(**client_dict, company_id=company_id)
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

    async def get_client_by_id(self, client_id: str, company_id: UUID, user_role: UserRole) -> ClientFullResponse | None:
        """Получает полную информацию о клиенте с его контактами (только для своей компании)"""
        stmt = select(Client).options(selectinload(Client.contacts)).where(Client.id == UUID(client_id), Client.company_id == company_id)
        result = await self.db.execute(stmt)
        client = result.scalars().first()

        if not client:
            return None

        return ClientFullResponse.model_validate(client)

    async def get_clients(self, filters: ClientFilters, company_id: UUID) -> ClientListResponse:
        """Получает список клиентов с фильтрацией и пагинацией (только для своей компании)"""

        case_counts_subq = (
            select(
                Case.client_id,
                func.count(Case.id).label("total_cases"),
                func.sum(case((Case.status == "in_work", 1), else_=0)).label("active_cases"),
            )
            .group_by(Case.client_id)
            .subquery()
        )

        stmt = (
            select(Client, case_counts_subq.c.total_cases, case_counts_subq.c.active_cases)
            .outerjoin(case_counts_subq, Client.id == case_counts_subq.c.client_id)
            .where(Client.company_id == company_id)
        )

        if filters.type:
            stmt = stmt.where(Client.type == filters.type)

        if filters.search:
            search_filter = or_(
                Client.name.ilike(f"%{filters.search}%"),
                Client.inn.ilike(f"%{filters.search}%"),
            )
            stmt = stmt.where(search_filter)

        count_stmt = select(func.count()).select_from(stmt.options(joinedload("*")).subquery())
        total_count = (await self.db.execute(count_stmt)).scalar() or 0

        offset = (filters.page - 1) * filters.limit
        stmt = stmt.order_by(Client.created_at.desc()).offset(offset).limit(filters.limit)

        result = await self.db.execute(stmt)
        rows = result.all()

        clients_with_counts = []
        for row in rows:
            client_data = row.Client
            client_data.active_cases = row.active_cases or 0
            client_data.total_cases = row.total_cases or 0
            clients_with_counts.append(ClientShortResponse.model_validate(client_data))

        total_pages = max(1, (total_count + filters.limit - 1) // filters.limit)

        return ClientListResponse(
            items=clients_with_counts,
            total=total_count,
            page=filters.page,
            size=len(clients_with_counts),
            pages=total_pages,
        )

    async def update_client(self, client_id: str, update_data: ClientUpdate, company_id: UUID, user_role: UserRole) -> ClientFullResponse | None:
        """Обновляет данные клиента (только для своей компании)"""

        if user_role == UserRole.EXPERT:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Эксперт не может обновлять данные клиента")

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
        """Удаляет клиента (каскадно удалятся и контакты из-за ondelete='CASCADE') (только для своей компании)"""

        if user_role == UserRole.EXPERT:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Эксперт не может удалять клиентов")

        stmt = select(Client).where(Client.id == UUID(client_id), Client.company_id == company_id)
        result = await self.db.execute(stmt)
        client = result.scalars().first()

        if not client:
            return False

        await self.db.delete(client)
        await self.db.commit()
        return True

    async def search_name(self, query: str, company_id: UUID) -> Sequence[tuple[UUID, str]]:
        stmt = (
            select(Client.id, Client.name)
            .where(
                Client.company_id == company_id,
                or_(Client.name.istartswith(query), Client.short_name.istartswith(query)),
            )
            .limit(5)
        )
        result = await self.db.execute(stmt)
        rows = result.all()
        return [(row[0], row[1]) for row in rows]
