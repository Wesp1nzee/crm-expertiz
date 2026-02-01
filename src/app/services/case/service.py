import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.services.case.models import Case, CaseStatus
from src.app.services.case.schemas import (
    CaseCreateRequest,
    CaseResponse,
    CasesSummary,
    CaseUpdateRequest,
    GetCasesQuery,
    GetCasesResponse,
    PaginationInfo,
)
from src.app.services.client.models import Client
from src.app.services.user.models import User, UserRole


class CaseService:
    def __init__(self, db_session: AsyncSession) -> None:
        self.db = db_session

    async def create_case(self, case_data: CaseCreateRequest, user_id: uuid.UUID, user_role: UserRole) -> CaseResponse:
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

        data["created_by"] = user_id

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

    async def get_case_by_id(self, case_id: str, user_id: uuid.UUID, user_role: UserRole) -> CaseResponse | None:
        """Получает дело по ID (эксперт может получить только свои дела)"""
        stmt = select(Case).where(Case.id == uuid.UUID(case_id), Case.deleted_at.is_(None))

        if user_role == UserRole.EXPERT:
            stmt = stmt.where(Case.assigned_user_id == user_id)

        result = await self.db.execute(stmt)
        case = result.scalars().first()

        if not case:
            return None

        return CaseResponse.model_validate(case)

    async def update_case(self, case_id: str, update_data: CaseUpdateRequest, user_id: uuid.UUID, user_role: UserRole) -> CaseResponse | None:
        """Обновляет дело (только для своей компании)"""
        if user_role == UserRole.EXPERT:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Эксперт не может обновлять данные дела")

        stmt = select(Case).where(Case.id == uuid.UUID(case_id), Case.deleted_at.is_(None))
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

    async def soft_delete_case(self, case_id: str, user_id: uuid.UUID, user_role: UserRole) -> bool:
        """Мягкое удаление дела (только для своей компании)"""
        if user_role == UserRole.EXPERT:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Эксперт не может удалять дела")

        stmt = select(Case).where(Case.id == uuid.UUID(case_id), Case.deleted_at.is_(None))
        result = await self.db.execute(stmt)
        case = result.scalars().first()

        if not case:
            return False

        case.deleted_at = datetime.utcnow()
        await self.db.commit()
        return True

    async def get_cases(self, query_params: GetCasesQuery, user_id: uuid.UUID, user_role: UserRole) -> GetCasesResponse:
        """Получает список дел с фильтрацией, пагинацией и статистикой (эксперт видит только свои дела)"""
        stmt = select(Case).where(Case.deleted_at.is_(None))

        if user_role == UserRole.EXPERT:
            stmt = stmt.where(Case.assigned_user_id == user_id)

        if query_params.status:
            stmt = stmt.where(Case.status.in_(query_params.status))
        if query_params.expert_id:
            stmt = stmt.where(Case.assigned_user_id == query_params.expert_id)
        if query_params.client_id:
            stmt = stmt.where(Case.client_id == query_params.client_id)
        if query_params.start_date:
            stmt = stmt.where(Case.start_date >= query_params.start_date)
        if query_params.end_date:
            stmt = stmt.where(Case.start_date <= query_params.end_date)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_count = (await self.db.execute(count_stmt)).scalar() or 0

        offset = (query_params.page - 1) * query_params.limit
        stmt = stmt.offset(offset).limit(query_params.limit)

        result = await self.db.execute(stmt)
        cases = result.scalars().all()

        now = datetime.utcnow()

        inactive_statuses = [
            CaseStatus.executed,
            CaseStatus.cancelled,
            CaseStatus.archive,
        ]
        active_stmt = select(func.count()).select_from(Case).where(Case.deleted_at.is_(None), Case.status.notin_(inactive_statuses))
        if user_role == UserRole.EXPERT:
            active_stmt = active_stmt.where(Case.assigned_user_id == user_id)

        active_count = (await self.db.execute(active_stmt)).scalar() or 0

        overdue_stmt = (
            select(func.count()).select_from(Case).where(Case.deleted_at.is_(None), Case.status.notin_(inactive_statuses), Case.deadline < now)
        )
        if user_role == UserRole.EXPERT:
            overdue_stmt = overdue_stmt.where(Case.assigned_user_id == user_id)

        overdue_count = (await self.db.execute(overdue_stmt)).scalar() or 0

        total_pages = max(1, (total_count + query_params.limit - 1) // query_params.limit)

        return GetCasesResponse(
            data=[CaseResponse.model_validate(c) for c in cases],
            pagination=PaginationInfo(
                total=total_count,
                page=query_params.page,
                limit=query_params.limit,
                total_pages=total_pages,
            ),
            summary=CasesSummary(
                active=active_count,
                overdue=overdue_count,
                completed=total_count - active_count,
            ),
        )
