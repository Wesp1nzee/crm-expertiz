from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import asc, desc, func, select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import Select

from src.app.services.case.models import Case, CaseStatus
from src.app.services.case.schemas import (
    CaseCreateRequest,
    CaseDetailsResponse,
    CaseResponse,
    CasesSummary,
    CaseSuggestionResponse,
    CaseUpdateRequest,
    ClientResponse,
    DocumentResponse,
    GetCasesQuery,
    GetCasesResponse,
    MailMessageResponse,
    PaginationInfo,
    SortField,
    SortOrder,
    UserResponse,
)
from src.app.services.client.models import Client
from src.app.services.document.models import Document
from src.app.services.user.models import User, UserRole


class CaseService:
    def __init__(self, db_session: AsyncSession) -> None:
        self.db = db_session

    async def create_case(self, case_data: CaseCreateRequest, user_id: UUID, user_role: UserRole) -> CaseResponse:
        """Создает новое дело"""
        if user_role == UserRole.EXPERT:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Эксперт не может создавать новые дела")

        if case_data.deadline < case_data.start_date:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Срок выполнения не может быть раньше даты начала")

        existing_case_query = await self.db.execute(select(Case).where(Case.number == case_data.number))
        if existing_case_query.scalar():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Дело с номером '{case_data.number}' уже существует")

        existing_case_number_query = await self.db.execute(select(Case).where(Case.case_number == case_data.case_number))
        if existing_case_number_query.scalar():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"Дело с номером производства '{case_data.case_number}' уже существует"
            )

        client = await self.db.get(Client, case_data.client_id)
        if not client:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Клиент с ID {case_data.client_id} не найден")

        if case_data.assigned_user_id:
            user = await self.db.get(User, case_data.assigned_user_id)
            if not user:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Эксперт с ID {case_data.assigned_user_id} не найден")

        data = case_data.model_dump()

        decimal_fields = ["cost", "bank_transfer_amount", "cash_amount", "remaining_debt"]
        for field in decimal_fields:
            if field in data and data[field] is not None:
                try:
                    data[field] = Decimal(str(data[field]))
                except (ValueError, TypeError) as err:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Некорректное значение для поля '{field}'") from err

        total_payments = data.get("bank_transfer_amount", Decimal("0")) + data.get("cash_amount", Decimal("0"))
        if total_payments > data.get("cost", Decimal("0")):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Сумма платежей не может превышать общую стоимость дела")

        data["remaining_debt"] = data["cost"] - total_payments

        if data.get("assigned_user_id") == "":
            data["assigned_user_id"] = None

        try:
            case = Case(**data)
            self.db.add(case)
            await self.db.commit()
            await self.db.refresh(case)

            return CaseResponse.model_validate(case)

        except Exception as db_error:
            await self.db.rollback()
            print(f"Database error during case creation: {db_error}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Ошибка при сохранении дела в базе данных"
            ) from db_error

    async def get_case_details(
        self,
        case_id: str,
        user_id: UUID,
        user_role: str,
    ) -> CaseDetailsResponse | None:
        """
        Возвращает полную информацию о деле со всеми связями.
        Учитывает RBAC: эксперты видят только свои дела.
        """
        case_uuid = UUID(case_id) if isinstance(case_id, str) else case_id
        stmt = (
            select(Case)
            .options(
                selectinload(Case.client).selectinload(Client.contacts),
                selectinload(Case.assigned_user),
                selectinload(Case.documents).selectinload(Document.folder),
                selectinload(Case.documents).selectinload(Document.uploaded_by),
                selectinload(Case.mail_messages),
            )
            .where(Case.id == case_uuid)
        )

        if user_role not in {"admin", "ceo", "accountant"}:
            stmt = stmt.where(Case.assigned_user_id == user_id)

        try:
            result = await self.db.execute(stmt)
            case = result.scalar_one()
        except NoResultFound:
            return None

        case_schema = CaseResponse.model_validate(case)
        client_schema = ClientResponse.model_validate(case.client)
        assigned_experts_schemas = [UserResponse.model_validate(case.assigned_user)] if case.assigned_user else []
        documents_schemas = [DocumentResponse.model_validate(doc) for doc in case.documents]
        events_schemas = [MailMessageResponse.model_validate(msg) for msg in case.mail_messages]

        return CaseDetailsResponse(
            case=case_schema,
            client=client_schema,
            assigned_experts=assigned_experts_schemas,
            documents=documents_schemas,
            events=events_schemas,
        )

    async def update_case(self, case_id: str, update_data: CaseUpdateRequest, user_id: UUID, user_role: UserRole) -> CaseResponse | None:
        """Обновляет дело (только для своей компании)"""
        if user_role == UserRole.EXPERT:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Эксперт не может обновлять данные дела")

        case_uuid = UUID(case_id) if isinstance(case_id, str) else case_id
        stmt = select(Case).where(Case.id == case_uuid, Case.deleted_at.is_(None))
        result = await self.db.execute(stmt)
        case = result.scalars().first()

        if not case:
            return None

        update_dict = update_data.model_dump(exclude_unset=True)
        for field, value in update_dict.items():
            if hasattr(case, field):
                setattr(case, field, value)

        if case.deadline < case.start_date:
            raise ValueError("Deadline cannot be before start date")

        await self.db.commit()
        await self.db.refresh(case)

        return CaseResponse.model_validate(case)

    async def soft_delete_case(self, case_id: str, user_id: UUID, user_role: UserRole) -> bool:
        """Мягкое удаление дела (только для своей компании)"""
        if user_role == UserRole.EXPERT:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Эксперт не может удалять дела")

        case_uuid = UUID(case_id) if isinstance(case_id, str) else case_id
        stmt = select(Case).where(Case.id == case_uuid, Case.deleted_at.is_(None))
        result = await self.db.execute(stmt)
        case = result.scalars().first()

        if not case:
            return False

        case.deleted_at = datetime.utcnow()
        await self.db.commit()
        return True

    async def get_cases(self, query_params: GetCasesQuery, user_id: UUID, user_role: UserRole) -> GetCasesResponse:
        """Получает список дел с фильтрацией, пагинацией, сортировкой и статистикой (эксперт видит только свои дела)"""

        base_count_stmt = select(func.count(Case.id)).where(Case.deleted_at.is_(None))

        if user_role == UserRole.EXPERT:
            base_count_stmt = base_count_stmt.where(Case.assigned_user_id == user_id)

        filters: list[tuple[Any | None, Callable[[Any], Any] | None]] = [
            (query_params.status, lambda q: Case.status.in_(q) if isinstance(q, list) else Case.status == q),
            (query_params.expert_id, lambda q: Case.assigned_user_id == q),
            (query_params.client_id, lambda q: Case.client_id == q),
            (query_params.start_date, lambda q: Case.start_date >= q),
            (query_params.end_date, lambda q: Case.start_date <= q),
            (query_params.case_type, lambda q: Case.case_type.ilike(f"%{q}%")),
            (query_params.object_type, lambda q: Case.object_type.ilike(f"%{q}%")),
            (query_params.authority, lambda q: Case.authority.ilike(f"%{q}%")),
            (query_params.object_address, lambda q: Case.object_address.ilike(f"%{q}%")),
            (query_params.number, lambda q: Case.number == q),
            (query_params.case_number, lambda q: Case.case_number == q),
            (query_params.min_cost, lambda q: Case.cost >= q),
            (query_params.max_cost, lambda q: Case.cost <= q),
            (query_params.min_remaining_debt, lambda q: Case.remaining_debt >= q),
            (query_params.max_remaining_debt, lambda q: Case.remaining_debt <= q),
            (query_params.completion_start_date, lambda q: Case.completion_date >= q),
            (query_params.completion_end_date, lambda q: Case.completion_date <= q),
            (query_params.deadline_start_date, lambda q: Case.deadline >= q),
            (query_params.deadline_end_date, lambda q: Case.deadline <= q),
        ]

        for param_value, condition_func in filters:
            if param_value is not None and condition_func is not None:
                condition = condition_func(param_value)
                base_count_stmt = base_count_stmt.where(condition)

        search_condition = None
        if query_params.search:
            search_term = f"%{query_params.search}%"
            search_condition = (
                (Case.number.ilike(search_term))
                | (Case.case_number.ilike(search_term))
                | (Case.authority.ilike(search_term))
                | (Case.object_address.ilike(search_term))
                | (Case.plaintiff.ilike(search_term))
                | (Case.defendant.ilike(search_term))
                | (Case.remarks.ilike(search_term))
            )
            base_count_stmt = base_count_stmt.outerjoin(Client, Case.client_id == Client.id)
            search_condition = search_condition | (Client.name.ilike(search_term))
            base_count_stmt = base_count_stmt.where(search_condition)

        total_count_result = await self.db.execute(base_count_stmt)
        total_count = total_count_result.scalar() or 0

        inactive_statuses = [CaseStatus.executed, CaseStatus.cancelled, CaseStatus.archive]
        active_count_stmt = select(func.count(Case.id)).where(Case.id.in_(select(Case.id).where(Case.deleted_at.is_(None))))

        if user_role == UserRole.EXPERT:
            active_count_stmt = active_count_stmt.where(Case.assigned_user_id == user_id)

        active_count_stmt = active_count_stmt.where(Case.status.notin_(inactive_statuses))
        active_count_result = await self.db.execute(active_count_stmt)
        active_count = active_count_result.scalar() or 0

        now = datetime.now()
        overdue_count_stmt = select(func.count(Case.id)).where(
            Case.id.in_(select(Case.id).where(Case.deleted_at.is_(None))), Case.status.notin_(inactive_statuses), Case.deadline < now
        )

        if user_role == UserRole.EXPERT:
            overdue_count_stmt = overdue_count_stmt.where(Case.assigned_user_id == user_id)

        overdue_count_result = await self.db.execute(overdue_count_stmt)
        overdue_count = overdue_count_result.scalar() or 0

        completed_count = max(0, total_count - active_count)

        stmt: Select[Any] = (
            select(Case)
            .outerjoin(Client, Case.client_id == Client.id)
            .outerjoin(User, Case.assigned_user_id == User.id)
            .options(
                selectinload(Case.assigned_user),
                selectinload(Case.client),
            )
            .where(Case.deleted_at.is_(None))
        )

        if user_role == UserRole.EXPERT:
            stmt = stmt.where(Case.assigned_user_id == user_id)

        for param_value, condition_func in filters:
            if param_value is not None and condition_func is not None:
                condition = condition_func(param_value)
                stmt = stmt.where(condition)

        # Добавляем условие поиска в основной запрос
        if query_params.search:
            # Повторно создаем условие поиска для основного запроса
            search_term = f"%{query_params.search}%"
            case_search_condition = (
                (Case.number.ilike(search_term))
                | (Case.case_number.ilike(search_term))
                | (Case.authority.ilike(search_term))
                | (Case.object_address.ilike(search_term))
                | (Case.plaintiff.ilike(search_term))
                | (Case.defendant.ilike(search_term))
                | (Case.remarks.ilike(search_term))
            )
            # Для основного запроса уже есть JOIN с Client
            case_search_condition = case_search_condition | (Client.name.ilike(search_term))
            stmt = stmt.where(case_search_condition)

        # Сортировка
        if query_params.sort_field and query_params.sort_order:
            sort_column = None
            if query_params.sort_field == SortField.CLIENT_NAME:
                sort_column = Client.name
            elif query_params.sort_field == SortField.EXPERT_NAME:
                sort_column = User.full_name
            else:
                sort_column = getattr(Case, query_params.sort_field.value)

            stmt = stmt.order_by(asc(sort_column) if query_params.sort_order == SortOrder.ASC else desc(sort_column))
        else:
            stmt = stmt.order_by(desc(Case.created_at))

        # Пагинация
        offset = (query_params.page - 1) * query_params.limit
        stmt = stmt.offset(offset).limit(query_params.limit)

        result = await self.db.execute(stmt)
        cases = result.scalars().all()
        total_pages = max(1, (total_count + query_params.limit - 1) // query_params.limit)

        case_responses = [
            CaseResponse.model_validate(case).model_copy(
                update={"assigned_expert": UserResponse.model_validate(case.assigned_user) if case.assigned_user else None}
            )
            for case in cases
        ]

        return GetCasesResponse(
            data=case_responses,
            pagination=PaginationInfo(
                total=total_count,
                page=query_params.page,
                limit=query_params.limit,
                total_pages=total_pages,
            ),
            summary=CasesSummary(
                active=active_count,
                overdue=overdue_count,
                completed=completed_count,
            ),
        )

    async def suggest_cases(self, query: str, user_id: UUID, user_role: UserRole) -> list[CaseSuggestionResponse]:
        """
        Возвращает подсказки по делам, основываясь на поисковом запросе.
        Эксперты видят только свои дела.
        """
        search_pattern = f"%{query}%"

        stmt = (
            select(
                Case.id,
                Case.number,
                Case.case_number,
            )
            .where(
                Case.deleted_at.is_(None),
                (Case.number.ilike(search_pattern)) | (Case.case_number.ilike(search_pattern)),
            )
            .order_by(Case.updated_at.desc())
            .limit(5)
        )

        if user_role == UserRole.EXPERT:
            stmt = stmt.where(Case.assigned_user_id == user_id)

        result = await self.db.execute(stmt)
        rows = result.all()

        suggestions = []
        for row in rows:
            suggestion = CaseSuggestionResponse(
                id=row.id,
                number=row.number,
                case_number=row.case_number,
            )
            suggestions.append(suggestion)

        return suggestions
