import math
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from src.app.core.schemas import PaginatedResponse, PaginationMeta
from src.app.services.case.models import Case
from src.app.services.client.models import Client, Contact
from src.app.services.client.schemas import (
    ClientCreate,
    ClientFullResponse,
    ClientShortResponse,
    ClientUpdate,
    ContactCreate,
    ContactUpdate,
    RecentEmailResponse,
)
from src.app.services.mail.models import MailMessage
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

        # Собираем все email адреса клиента и его контактов
        client_emails = set()
        if client.email:
            client_emails.add(client.email.lower())
        for contact in client.contacts:
            if contact.email:
                client_emails.add(contact.email.lower())

        # Получаем последние 10 писем, связанных с клиентом:
        # 1. Письма, где клиент - отправитель или получатель (по email)
        # 2. Письма, привязанные к делам клиента
        # 3. Письма, где sender_email совпадает с email клиента
        from src.app.services.mail.models import MailRecipient

        # Подзапрос для получения писем через дела клиента
        cases_email_subq = select(MailMessage.id).join(Case, MailMessage.case_id == Case.id).where(Case.client_id == client.id)

        # Подзапрос для получения писем через email клиента (как отправитель)
        sender_email_subq = select(MailMessage.id).where(MailMessage.sender_email.in_(list(client_emails))) if client_emails else None

        # Подзапрос для получения писем через email клиента (как получатель)
        recipient_email_subq = (
            select(MailRecipient.message_id).where(MailRecipient.email_address.in_(list(client_emails))) if client_emails else None
        )

        or_conditions: list[Any] = [MailMessage.id.in_(cases_email_subq)]
        if sender_email_subq is not None:
            or_conditions.append(MailMessage.id.in_(sender_email_subq))
        if recipient_email_subq is not None:
            or_conditions.append(MailMessage.id.in_(recipient_email_subq))

        emails_stmt = (
            select(
                MailMessage.id,
                MailMessage.thread_id,
                MailMessage.subject,
                MailMessage.sender_email,
                MailMessage.sender_name,
                MailMessage.message_type,
                MailMessage.folder,
                MailMessage.is_read,
                MailMessage.sent_at,
                MailMessage.case_id,
                Case.case_number,
            )
            .outerjoin(Case, MailMessage.case_id == Case.id)
            .where(
                MailMessage.company_id == company_id,
                or_(*or_conditions),
            )
            .order_by(MailMessage.sent_at.desc())
            .limit(10)
        )
        emails_result = await self.db.execute(emails_stmt)
        recent_emails = [
            RecentEmailResponse(
                id=row.id,
                thread_id=row.thread_id,
                subject=row.subject,
                sender_email=row.sender_email,
                sender_name=row.sender_name,
                message_type=row.message_type.value,
                folder=row.folder.value,
                is_read=row.is_read,
                sent_at=row.sent_at,
                case_id=row.case_id,
                case_number=row.case_number,
            )
            for row in emails_result.all()
        ]

        response = ClientFullResponse.model_validate(client)
        response.recent_emails = recent_emails
        return response

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

    async def search_name(self, query: str, company_id: UUID, limit: int = 10) -> Sequence[tuple[UUID, str]]:
        q = query.strip().upper()
        if not q:
            stmt = select(Client.id, Client.name).where(Client.company_id == company_id).order_by(Client.created_at.desc()).limit(5)
            result = await self.db.execute(stmt)
            return [(row.id, row.name) for row in result.all()]

        prefix_pattern = f"{q}%"
        word_pattern = f"% {q}%"
        contains_pattern = f"%{q}%"

        name_up = func.upper(Client.name)
        short_name_up = func.coalesce(func.upper(Client.short_name), "")

        def bool_to_float(condition: ColumnElement[bool]) -> ColumnElement[float]:
            return case((condition, 1.0), else_=0.0)

        relevance = (
            bool_to_float(or_(name_up.ilike(prefix_pattern), short_name_up.ilike(prefix_pattern))) * 4
            + bool_to_float(or_(name_up.ilike(word_pattern), short_name_up.ilike(word_pattern))) * 3
            + bool_to_float(or_(name_up.ilike(contains_pattern), short_name_up.ilike(contains_pattern))) * 2
            + func.greatest(
                func.similarity(name_up, q),
                func.coalesce(func.similarity(func.upper(Client.short_name), q), 0.0),
            )
        )

        stmt = (
            select(Client.id, Client.name)
            .where(
                Client.company_id == company_id,
                or_(
                    name_up.ilike(contains_pattern),
                    short_name_up.ilike(contains_pattern),
                    name_up.ilike(word_pattern),
                    short_name_up.ilike(word_pattern),
                    func.similarity(name_up, q) > 0.2,
                    func.similarity(short_name_up, q) > 0.2,
                ),
            )
            .order_by(relevance.desc(), Client.name.asc())
            .limit(limit)
        )

        result = await self.db.execute(stmt)
        return [(row.id, row.name) for row in result.all()]

    async def create_contact(self, client_id: str, contact_data: ContactCreate, company_id: UUID, user_role: UserRole) -> Contact:
        """Создает контакт для клиента"""
        if user_role == UserRole.EXPERT:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Эксперт не может создавать контакты",
            )

        stmt = select(Client).where(Client.id == UUID(client_id), Client.company_id == company_id)
        result = await self.db.execute(stmt)
        client = result.scalars().first()

        if not client:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Клиент не найден")

        contact = Contact(
            **contact_data.model_dump(exclude={"client_id"}),
            client_id=client.id,
            company_id=company_id,
        )
        self.db.add(contact)
        await self.db.commit()
        await self.db.refresh(contact)
        return contact

    async def get_contacts(self, client_id: str, company_id: UUID) -> Sequence[Contact]:
        """Получает все контакты клиента"""
        stmt = select(Contact).where(Contact.client_id == UUID(client_id), Contact.company_id == company_id)
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def update_contact(self, contact_id: str, update_data: ContactUpdate, company_id: UUID, user_role: UserRole) -> Contact:
        """Обновляет контакт"""
        if user_role == UserRole.EXPERT:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Эксперт не может обновлять контакты",
            )

        stmt = select(Contact).where(Contact.id == UUID(contact_id), Contact.company_id == company_id)
        result = await self.db.execute(stmt)
        contact = result.scalars().first()

        if not contact:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Контакт не найден")

        update_dict = update_data.model_dump(exclude_unset=True)
        for field, value in update_dict.items():
            setattr(contact, field, value)

        await self.db.commit()
        await self.db.refresh(contact)
        return contact

    async def delete_contact(self, contact_id: str, company_id: UUID, user_role: UserRole) -> bool:
        """Удаляет контакт"""
        if user_role == UserRole.EXPERT:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Эксперт не может удалять контакты",
            )

        stmt = select(Contact).where(Contact.id == UUID(contact_id), Contact.company_id == company_id)
        result = await self.db.execute(stmt)
        contact = result.scalars().first()

        if not contact:
            return False

        await self.db.delete(contact)
        await self.db.commit()
        return True
