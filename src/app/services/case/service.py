import uuid
from datetime import datetime

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


class CaseService:
    def __init__(self, db_session: AsyncSession) -> None:
        self.db = db_session

    async def create_case(self, case_data: CaseCreateRequest) -> CaseResponse:
        """Создает новое дело"""
        if case_data.deadline < case_data.start_date:
            raise ValueError("Deadline cannot be before start date")

        data = case_data.model_dump(exclude={"id"})
        case = Case(**data)

        self.db.add(case)
        await self.db.commit()
        await self.db.refresh(case)

        return CaseResponse.model_validate(case)

    async def get_case_by_id(self, case_id: str) -> CaseResponse | None:
        """Получает дело по ID"""
        stmt = select(Case).where(
            Case.id == uuid.UUID(case_id), Case.deleted_at.is_(None)
        )
        result = await self.db.execute(stmt)
        case = result.scalars().first()

        if not case:
            return None

        return CaseResponse.model_validate(case)

    async def update_case(
        self, case_id: str, update_data: CaseUpdateRequest
    ) -> CaseResponse | None:
        """Обновляет дело"""
        stmt = select(Case).where(
            Case.id == uuid.UUID(case_id), Case.deleted_at.is_(None)
        )
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

    async def soft_delete_case(self, case_id: str) -> bool:
        """Мягкое удаление дела"""
        stmt = select(Case).where(
            Case.id == uuid.UUID(case_id), Case.deleted_at.is_(None)
        )
        result = await self.db.execute(stmt)
        case = result.scalars().first()

        if not case:
            return False

        case.deleted_at = datetime.utcnow()
        await self.db.commit()
        return True

    async def get_cases(self, query_params: GetCasesQuery) -> GetCasesResponse:
        """Получает список дел с фильтрацией, пагинацией и статистикой"""
        stmt = select(Case).where(Case.deleted_at.is_(None))

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

        active_stmt = (
            select(func.count())
            .select_from(Case)
            .where(
                Case.deleted_at.is_(None),
                Case.status.notin_(inactive_statuses),
            )
        )
        active_count = (await self.db.execute(active_stmt)).scalar() or 0

        overdue_stmt = (
            select(func.count())
            .select_from(Case)
            .where(
                Case.deleted_at.is_(None),
                Case.status.notin_(inactive_statuses),
                Case.deadline < now,
            )
        )

        overdue_count = (await self.db.execute(overdue_stmt)).scalar() or 0

        total_pages = max(
            1, (total_count + query_params.limit - 1) // query_params.limit
        )

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
