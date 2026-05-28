import logging
import math
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, TypeVar
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import asc, delete, desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import Select

from src.app.services.case.models import Case, CaseStatus, case_experts
from src.app.services.case.schemas import (
    AssignExpertsRequest,
    CaseCreateRequest,
    CaseDetailsResponse,
    CaseResponse,
    CasesPaginationMeta,
    CaseSuggestionResponse,
    CaseUpdateRequest,
    ClientResponse,
    DocumentResponse,
    EfficiencyMetrics,
    FinancialSummaryResponse,
    FolderResponse,
    GetCasesQuery,
    GetCasesResponse,
    MailMessageDetailResponse,
    RecentCaseItem,
    SortField,
    SortOrder,
    UserResponse,
)
from src.app.services.client.models import Client
from src.app.services.document.models import Document, Folder
from src.app.services.mail.models import MailMessage
from src.app.services.user.models import User, UserRole

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=tuple[Any, ...])


class CaseService:
    def __init__(self, db_session: AsyncSession) -> None:
        self.db = db_session

    def _expert_filter(self, stmt: Select[T], user_id: UUID) -> Select[T]:
        return stmt.where(Case.id.in_(select(case_experts.c.case_id).where(case_experts.c.user_id == user_id)))

    async def _load_case_or_404(self, case_id: UUID, company_id: UUID) -> Case:
        stmt = (
            select(Case).options(selectinload(Case.experts)).where(Case.id == case_id, Case.company_id == company_id, Case.deleted_at.is_(None))
        )
        result = await self.db.execute(stmt)
        case = result.scalar_one_or_none()
        if not case:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Дело не найдено")
        return case

    async def get_financial_summary(self, user_id: UUID, user_role: UserRole, company_id: UUID) -> FinancialSummaryResponse:
        now = datetime.now(ZoneInfo("UTC"))
        month_ago = now - timedelta(days=30)
        two_months_ago = now - timedelta(days=60)

        query = select(Case).where(Case.deleted_at.is_(None), Case.company_id == company_id)
        if user_role == UserRole.EXPERT:
            query = self._expert_filter(query, user_id)

        result = await self.db.execute(query)
        all_cases = result.scalars().all()

        recent_query = select(Case).where(Case.deleted_at.is_(None), Case.company_id == company_id).order_by(Case.created_at.desc()).limit(6)
        if user_role == UserRole.EXPERT:
            recent_query = self._expert_filter(recent_query, user_id)

        recent_result = await self.db.execute(recent_query)
        recent_cases_db = recent_result.scalars().all()

        completed_cases = [c for c in all_cases if c.status != CaseStatus.in_work]
        active_cases = [c for c in all_cases if c.status == CaseStatus.in_work]

        total_revenue = sum(Decimal(str(c.cost)) for c in completed_cases)

        durations = [(c.completion_date - c.start_date).days for c in completed_cases if c.completion_date and c.start_date]
        avg_time = sum(durations) / len(durations) if durations else 0

        def get_conv_rate(start_date: datetime, end_date: datetime) -> float:
            period_cases = [c for c in all_cases if start_date <= c.created_at < end_date]
            if not period_cases:
                return 0
            executed_in_period = [c for c in period_cases if c.status == CaseStatus.executed]
            return len(executed_in_period) / len(period_cases) * 100

        current_conv = get_conv_rate(month_ago, now)
        past_conv = get_conv_rate(two_months_ago, month_ago)
        conv_trend = current_conv - past_conv

        recent_completed = [c for c in completed_cases if c.completion_date and c.completion_date >= month_ago]
        recent_completed_ids = [c.id for c in recent_completed]

        pending_cases = [c for c in all_cases if c.remaining_debt > 0]
        pending_amount = sum(Decimal(str(c.remaining_debt)) for c in pending_cases)

        unique_experts_count = 0
        if recent_completed_ids:
            experts_result = await self.db.execute(
                select(func.count(func.distinct(case_experts.c.user_id))).where(case_experts.c.case_id.in_(recent_completed_ids))
            )
            unique_experts_count = experts_result.scalar() or 0

        throughput = len(recent_completed) / unique_experts_count if unique_experts_count > 0 else 0

        actual_debt_cases = [c for c in all_cases if c.status in [CaseStatus.debt, CaseStatus.fssp] and c.remaining_debt > 0]
        actual_debt_amount = sum(Decimal(str(c.remaining_debt)) for c in actual_debt_cases)

        overdue_count = 0
        for case in active_cases:
            deadline = case.deadline
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=ZoneInfo("UTC"))
            if deadline < now:
                overdue_count += 1

        return FinancialSummaryResponse(
            total_revenue=total_revenue,
            pending_payments=len(pending_cases),
            pending_amount=pending_amount,
            actual_debt_amount=actual_debt_amount,
            average_case_cost=total_revenue / len(completed_cases) if completed_cases else Decimal("0.00"),
            total_cases=len(all_cases),
            completed_cases=len(completed_cases),
            active_cases=len(active_cases),
            overdue_cases=overdue_count,
            efficiency=EfficiencyMetrics(
                avg_completion_time=round(avg_time, 1),
                conversion_rate=round(current_conv, 1),
                conversion_trend=round(conv_trend, 1),
                throughput=round(throughput, 2),
            ),
            recent_cases=[
                RecentCaseItem(
                    id=c.id,
                    number=c.number,
                    case_number=c.case_number,
                    status=c.status,
                    cost=c.cost,
                    created_at=c.created_at,
                    client_id=c.client_id,
                )
                for c in recent_cases_db
            ],
        )

    async def create_case(self, case_data: CaseCreateRequest, user_id: UUID, user_role: UserRole, company_id: UUID) -> CaseResponse:
        if user_role == UserRole.EXPERT:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Эксперт не может создавать новые дела")

        if case_data.deadline < case_data.start_date:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Срок выполнения не может быть раньше даты начала")

        if case_data.execution_date and case_data.execution_date < case_data.start_date:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Дата выполнения не может быть раньше даты начала работ")

        if case_data.registration_date and case_data.registration_date > case_data.start_date:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Дата регистрации не может быть позже даты начала работ")

        existing = await self.db.execute(select(Case).where(Case.number == case_data.number, Case.company_id == company_id))
        if existing.scalar():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Дело с номером '{case_data.number}' уже существует")

        existing_cn = await self.db.execute(select(Case).where(Case.case_number == case_data.case_number, Case.company_id == company_id))
        if existing_cn.scalar():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"Дело с номером производства '{case_data.case_number}' уже существует"
            )

        client = await self.db.get(Client, case_data.client_id)
        if not client or client.company_id != company_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Клиент с ID {case_data.client_id} не найден")

        experts: list[User] = []
        if case_data.expert_ids:
            experts_result = await self.db.execute(select(User).where(User.id.in_(case_data.expert_ids), User.company_id == company_id))
            experts = list(experts_result.scalars().all())
            if len(experts) != len(case_data.expert_ids):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Один или несколько экспертов не найдены")

        data = case_data.model_dump(exclude={"expert_ids", "expert_painting", "archive_status", "parent_folder_id"})
        data["company_id"] = company_id

        decimal_fields = ["cost", "bank_transfer_amount", "cash_amount", "remaining_debt", "debit"]
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

        try:
            case = Case(**data)
            case.experts = experts
            self.db.add(case)

            await self.db.flush()

            effective_parent_id = None

            if case_data.parent_folder_id:
                parent_folder = await self.db.get(Folder, case_data.parent_folder_id)
                if not parent_folder or parent_folder.company_id != company_id:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Родительская папка не найдена")
                if user_role == UserRole.EXPERT and parent_folder.created_by_id != user_id:  # type: ignore[comparison-overlap]
                    if not (
                        parent_folder.case_id
                        and await self.db.execute(
                            select(case_experts.c.case_id).where(
                                case_experts.c.case_id == parent_folder.case_id, case_experts.c.user_id == user_id
                            )
                        )
                    ).scalar():
                        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет прав для размещения дела в этой папке")

                if parent_folder.case_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Нельзя разместить дело внутри папки, которая уже принадлежит другому делу",
                    )

                effective_parent_id = parent_folder.id

            root_folder = Folder(
                name=f"Дело №{case.number}", case_id=case.id, company_id=company_id, created_by_id=user_id, parent_id=effective_parent_id
            )
            self.db.add(root_folder)
            await self.db.flush()

            case.root_folder_id = root_folder.id
            await self.db.commit()
            await self.db.refresh(case)

            return self._to_case_response(case)

        except Exception as db_error:
            await self.db.rollback()
            logger.exception("CRITICAL: Ошибка при создании дела")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Ошибка при сохранении дела и структуры документов"
            ) from db_error

    async def get_case_details(self, case_id: UUID, user_id: UUID, user_role: str, company_id: UUID) -> CaseDetailsResponse:
        stmt = (
            select(Case)
            .options(
                selectinload(Case.client).selectinload(Client.contacts),
                selectinload(Case.experts),
                selectinload(Case.mail_messages).selectinload(MailMessage.content),
                selectinload(Case.mail_messages).selectinload(MailMessage.attachments),
            )
            .where(Case.id == case_id, Case.company_id == company_id)
        )

        if user_role == UserRole.EXPERT:
            stmt = self._expert_filter(stmt, user_id)

        result = await self.db.execute(stmt)
        case = result.scalar_one_or_none()

        if not case:
            raise HTTPException(status_code=404, detail="Дело не найдено")

        folders_stmt = (
            select(Folder)
            .options(selectinload(Folder.creator))
            .where(Folder.parent_id == case.root_folder_id, Folder.company_id == company_id)
            .order_by(Folder.created_at.asc())
        )
        folders_result = await self.db.execute(folders_stmt)
        subfolders = folders_result.scalars().all()

        documents_stmt = (
            select(Document)
            .options(selectinload(Document.uploaded_by), selectinload(Document.folder))
            .where(Document.folder_id == case.root_folder_id, Document.company_id == company_id)
        )
        docs_result = await self.db.execute(documents_stmt)
        root_documents = docs_result.scalars().all()

        mail_messages = [self._to_mail_message_detail(msg) for msg in case.mail_messages if not msg.is_deleted]

        return CaseDetailsResponse(
            case=self._to_case_response(case),
            client=ClientResponse.model_validate(case.client),
            experts=[UserResponse.model_validate(u) for u in case.experts],
            documents=[DocumentResponse.model_validate(doc) for doc in root_documents],
            messages=mail_messages,
            folders=[FolderResponse.model_validate(f) for f in subfolders],
        )

    def _to_mail_message_detail(self, msg: MailMessage) -> MailMessageDetailResponse:
        body_text = msg.content.body_text if msg.content else None
        body_html = msg.content.body_html if msg.content else None

        return MailMessageDetailResponse(
            id=msg.id,
            external_message_id=msg.external_message_id,
            thread_id=msg.thread_id,
            parent_id=msg.parent_id,
            user_id=msg.user_id,
            case_id=msg.case_id,
            sender_email=msg.sender_email,
            sender_name=msg.sender_name,
            reply_to=msg.reply_to,
            subject=msg.subject,
            folder=msg.folder.value if hasattr(msg.folder, "value") else str(msg.folder),
            message_type=msg.message_type.value if hasattr(msg.message_type, "value") else str(msg.message_type),
            status=msg.status.value if hasattr(msg.status, "value") else str(msg.status),
            is_read=msg.is_read,
            is_important=msg.is_important,
            is_starred=msg.is_starred,
            is_spam=msg.is_spam,
            is_archived=msg.is_archived,
            is_deleted=msg.is_deleted,
            size_bytes=msg.size_bytes,
            sent_at=msg.sent_at,
            processed_at=msg.processed_at,
            updated_at=msg.updated_at,
            body_text=body_text,
            body_html=body_html,
            attachment_count=len(msg.attachments) if msg.attachments else 0,
        )

    async def update_case(self, case_id: UUID, update_data: CaseUpdateRequest, user_role: UserRole, company_id: UUID) -> CaseResponse | None:
        if user_role == UserRole.EXPERT:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Эксперт не может обновлять данные дела")

        stmt = (
            select(Case).options(selectinload(Case.experts)).where(Case.id == case_id, Case.company_id == company_id, Case.deleted_at.is_(None))
        )
        result = await self.db.execute(stmt)
        case = result.scalars().first()

        if not case:
            return None

        update_dict = update_data.model_dump(exclude_unset=True)

        if "client_id" in update_dict and update_dict["client_id"] is not None:
            new_client_id = update_dict["client_id"]
            client = await self.db.get(Client, new_client_id)
            if not client or client.company_id != company_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail=f"Клиент с ID {new_client_id} не найден или не принадлежит компании"
                )

        new_start_date = update_dict.get("start_date", case.start_date)
        new_registration_date = update_dict.get("registration_date", case.registration_date)
        new_deadline = update_dict.get("deadline", case.deadline)
        new_execution_date = update_dict.get("execution_date", case.execution_date)

        if new_registration_date and new_start_date and new_registration_date > new_start_date:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Дата регистрации не может быть позже даты начала работ")

        if new_deadline and new_start_date and new_deadline < new_start_date:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Срок выполнения не может быть раньше даты начала")

        if new_execution_date and new_start_date and new_execution_date < new_start_date:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Дата выполнения не может быть раньше даты начала работ")

        for field, value in update_dict.items():
            if hasattr(case, field) and value is not None:
                setattr(case, field, value)

        await self.db.commit()
        await self.db.refresh(case)
        return self._to_case_response(case)

    async def assign_experts(self, case_id: UUID, data: AssignExpertsRequest, user_role: UserRole, company_id: UUID) -> CaseResponse:
        if user_role == UserRole.EXPERT:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Эксперт не может назначать других экспертов")

        case = await self._load_case_or_404(case_id, company_id)

        experts_result = await self.db.execute(select(User).where(User.id.in_(data.expert_ids), User.company_id == company_id))
        experts = list(experts_result.scalars().all())

        if len(experts) != len(data.expert_ids):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Один или несколько экспертов не найдены")

        case.experts = experts
        await self.db.commit()
        await self.db.refresh(case)
        return self._to_case_response(case)

    async def soft_delete_case(self, case_id: UUID, user_role: UserRole, company_id: UUID) -> bool:
        if user_role == UserRole.EXPERT:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет прав для удаления дела.")

        stmt = select(Case).where(Case.id == case_id, Case.company_id == company_id)
        result = await self.db.execute(stmt)
        case = result.scalars().first()

        if not case:
            return False

        try:
            folder_ids_stmt = select(Folder.id).where(Folder.case_id == case_id, Folder.company_id == company_id)
            folder_ids_result = await self.db.execute(folder_ids_stmt)
            folder_ids = list(folder_ids_result.scalars().all())

            if folder_ids:
                await self.db.execute(delete(Document).where(Document.folder_id.in_(folder_ids), Document.company_id == company_id))

            await self.db.execute(delete(Folder).where(Folder.case_id == case_id, Folder.company_id == company_id))

            await self.db.execute(
                update(MailMessage).where(MailMessage.case_id == case_id, MailMessage.company_id == company_id).values(case_id=None)
            )

            await self.db.execute(delete(case_experts).where(case_experts.c.case_id == case_id))

            await self.db.execute(delete(Case).where(Case.id == case_id))

            await self.db.commit()
            logger.info(f"Case {case_id} deleted successfully.")
            return True

        except Exception as e:
            await self.db.rollback()
            logger.exception(f"CRITICAL: Error deleting case {case_id}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Ошибка при удалении дела и связанных данных") from e

    async def get_cases(self, query_params: GetCasesQuery, user_id: UUID, user_role: UserRole, company_id: UUID) -> GetCasesResponse:
        base_where = [Case.deleted_at.is_(None), Case.company_id == company_id]

        expert_subquery = None
        if user_role == UserRole.EXPERT:
            expert_subquery = select(case_experts.c.case_id).where(case_experts.c.user_id == user_id)
            base_where.append(Case.id.in_(expert_subquery))

        if query_params.expert_id:
            expert_id_subquery = select(case_experts.c.case_id).where(case_experts.c.user_id == query_params.expert_id)
            base_where.append(Case.id.in_(expert_id_subquery))

        base_count_stmt = select(func.count(Case.id)).where(*base_where)

        filters: list[tuple[Any | None, Callable[[Any], Any]]] = [
            (query_params.status, lambda q: Case.status.in_(q) if isinstance(q, list) else Case.status == q),
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
            if param_value is not None:
                base_count_stmt = base_count_stmt.where(condition_func(param_value))

        search_condition = None
        if query_params.search:
            search_term = f"%{query_params.search}%"
            search_condition = (
                Case.number.ilike(search_term)
                | Case.case_number.ilike(search_term)
                | Case.authority.ilike(search_term)
                | Case.object_address.ilike(search_term)
                | Case.plaintiff.ilike(search_term)
                | Case.defendant.ilike(search_term)
                | Case.remarks.ilike(search_term)
            )
            base_count_stmt = base_count_stmt.outerjoin(Client, Case.client_id == Client.id).where(
                search_condition | Client.name.ilike(search_term)
            )

        total_count = (await self.db.execute(base_count_stmt)).scalar() or 0

        inactive_statuses = [CaseStatus.executed, CaseStatus.cancelled, CaseStatus.archive]
        active_count = (
            await self.db.execute(select(func.count(Case.id)).where(*base_where, Case.status.notin_(inactive_statuses)))
        ).scalar() or 0

        overdue_count = (
            await self.db.execute(
                select(func.count(Case.id)).where(*base_where, Case.status.notin_(inactive_statuses), Case.deadline < datetime.now())
            )
        ).scalar() or 0

        stmt = (
            select(Case)
            .outerjoin(Client, Case.client_id == Client.id)
            .options(selectinload(Case.experts), selectinload(Case.client))
            .where(*base_where)
        )

        for param_value, condition_func in filters:
            if param_value is not None:
                stmt = stmt.where(condition_func(param_value))

        if query_params.search and search_condition is not None:
            search_term = f"%{query_params.search}%"
            stmt = stmt.where(search_condition | Client.name.ilike(search_term))

        if query_params.sort_field and query_params.sort_order:
            if query_params.sort_field == SortField.CLIENT_NAME:
                sort_column = Client.name
            elif query_params.sort_field == SortField.EXPERT_NAME:
                stmt = stmt.outerjoin(case_experts, Case.id == case_experts.c.case_id).outerjoin(User, case_experts.c.user_id == User.id)
                sort_column = User.full_name
            else:
                sort_column = getattr(Case, query_params.sort_field.value)
            stmt = stmt.order_by(asc(sort_column) if query_params.sort_order == SortOrder.ASC else desc(sort_column))
        else:
            stmt = stmt.order_by(desc(Case.created_at))

        offset = (query_params.page - 1) * query_params.limit
        cases = (await self.db.execute(stmt.offset(offset).limit(query_params.limit))).scalars().all()

        total_pages = max(1, math.ceil(total_count / query_params.limit))

        return GetCasesResponse(
            items=[self._to_case_response(c) for c in cases],
            meta=CasesPaginationMeta(
                total_items=total_count,
                total_pages=total_pages,
                current_page=query_params.page,
                per_page=query_params.limit,
                has_next=query_params.page < total_pages,
                has_prev=query_params.page > 1,
                active=active_count,
                overdue=overdue_count,
                completed=max(0, total_count - active_count),
            ),
        )

    async def suggest_cases(self, query: str, user_id: UUID, user_role: UserRole, company_id: UUID) -> list[CaseSuggestionResponse]:
        search_pattern = f"%{query}%"
        stmt = (
            select(Case.id, Case.number, Case.case_number)
            .where(
                Case.deleted_at.is_(None),
                Case.company_id == company_id,
                (Case.number.ilike(search_pattern)) | (Case.case_number.ilike(search_pattern)),
            )
            .order_by(Case.updated_at.desc())
            .limit(5)
        )
        if user_role == UserRole.EXPERT:
            stmt = self._expert_filter(stmt, user_id)

        rows = (await self.db.execute(stmt)).all()
        return [CaseSuggestionResponse(id=r.id, number=r.number, case_number=r.case_number) for r in rows]

    def _to_case_response(self, case: Case) -> CaseResponse:
        return CaseResponse.model_validate(case).model_copy(update={"experts": [UserResponse.model_validate(u) for u in (case.experts or [])]})

    async def _is_descendant(self, potential_parent_id: UUID, folder_id: UUID) -> bool:
        """Проверяет, является ли folder_id потомком potential_parent_id"""
        if potential_parent_id == folder_id:
            return True

        base = select(Folder.parent_id).where(Folder.id == folder_id).cte(name="ancestors", recursive=True)
        recursive = select(Folder.parent_id).join(base, Folder.id == base.c.parent_id)
        all_ancestors = base.union_all(recursive)

        result = await self.db.execute(select(1).where(all_ancestors.c.parent_id == potential_parent_id).limit(1))
        return result.scalar_one_or_none() is not None
