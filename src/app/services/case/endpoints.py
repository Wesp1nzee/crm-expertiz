import io
import logging
import uuid
import zipfile
from collections.abc import AsyncGenerator
from datetime import datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.auth.deps import get_current_user
from src.app.core.database.session import get_db
from src.app.core.storage.s3 import s3_storage
from src.app.services.case.models import Case, CaseStatus
from src.app.services.case.schemas import (
    CaseCreateRequest,
    CaseDetailsResponse,
    CaseResponse,
    CaseSuggestionResponse,
    CaseUpdateRequest,
    FinancialSummaryResponse,
    GetCasesQuery,
    GetCasesResponse,
)
from src.app.services.case.service import CaseService
from src.app.services.document.models import Document
from src.app.services.document.service import DocumentService
from src.app.services.user.models import User, UserRole

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cases", tags=["Cases"])


@router.get(
    "/financial-summary",
    response_model=FinancialSummaryResponse,
    summary="Финансовая сводка",
    description="Возвращает финансовую статистику по делам",
)
async def get_financial_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FinancialSummaryResponse:
    """
    Возвращает финансовую сводку по делам текущего пользователя или всех дел в зависимости от роли.
    """
    cases_query = select(Case).where(Case.deleted_at.is_(None))

    if current_user.role == UserRole.EXPERT:
        cases_query = cases_query.where(Case.assigned_user_id == current_user.id)

    cases_result = await db.execute(cases_query)
    all_cases = cases_result.scalars().all()

    completed_cases = [case for case in all_cases if case.status != CaseStatus.in_work]
    total_revenue = sum(Decimal(str(case.cost)) for case in completed_cases) if completed_cases else Decimal("0.00")

    active_cases = [case for case in all_cases if case.status == CaseStatus.in_work]

    now = datetime.now(ZoneInfo("UTC"))
    overdue_cases = []
    for case in active_cases:
        deadline = case.deadline
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=ZoneInfo("UTC"))
        if deadline < now:
            overdue_cases.append(case)

    average_case_cost = Decimal("0.00")

    if len(completed_cases) > 0:
        average_case_cost = total_revenue / Decimal(len(completed_cases)) if total_revenue > 0 else Decimal("0.00")

    pending_cases = [case for case in all_cases if case.remaining_debt > 0]
    pending_payments = len(pending_cases)
    pending_amount = sum(Decimal(str(case.remaining_debt)) for case in pending_cases) if pending_cases else Decimal("0.00")

    return FinancialSummaryResponse(
        total_revenue=total_revenue,
        pending_payments=pending_payments,
        pending_amount=pending_amount,
        average_case_cost=average_case_cost,
        total_cases=len(all_cases),
        completed_cases=len(completed_cases),
        active_cases=len(active_cases),
        overdue_cases=len(overdue_cases),
    )


@router.get("/{case_id}/download-documents", summary="Скачать все документы по делу как ZIP-архив")
async def download_case_documents_as_zip(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """
    Скачивание всех документов, связанных с делом, как ZIP-архива.
    """
    case_result = await db.execute(select(Case).where(Case.id == case_id))
    case = case_result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Дело не найдено")

    service = DocumentService(db)

    async def generate_zip() -> AsyncGenerator[bytes]:
        buffer = io.BytesIO()

        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            documents_query = select(Document).where(Document.case_id == case_id)
            documents_result = await db.execute(documents_query)
            documents = documents_result.scalars().all()

            for doc in documents:
                if await service._check_document_access(doc, current_user.id, current_user.role):
                    file_content = await s3_storage.get_file_content(doc.file_path)

                    safe_doc_title = doc.title.replace('"', "").replace("'", "").replace(";", "").replace(",", "")
                    zip_path = safe_doc_title
                    zip_file.writestr(zip_path, file_content)

        buffer.seek(0)
        while True:
            chunk = buffer.read(8192)
            if not chunk:
                break
            yield chunk

    safe_case_number = case.number.replace('"', "").replace("'", "").replace(";", "").replace(",", "")
    filename = f"case_{safe_case_number}_documents.zip"
    return StreamingResponse(
        generate_zip(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{safe_case_number}_documents.zip"},
    )


@router.get(
    "/suggest",
    response_model=list[CaseSuggestionResponse],
    summary="Подсказки по делам",
    description="Возвращает подсказки по делам по поисковому запросу в полях number или case_number",
)
async def suggest_case(
    q: str = Query(..., min_length=1, description="Поисковый запрос (минимум 1 символ)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CaseSuggestionResponse]:
    service = CaseService(db)
    try:
        return await service.suggest_cases(q, current_user.id, current_user.role)
    except Exception as err:
        logger.exception("Error during case suggestion search")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при поиске дел",
        ) from err


@router.get(
    "",
    response_model=GetCasesResponse,
    summary="Получить список дел",
    description="Возвращает список дел с фильтрацией, пагинацией и статистикой",
)
async def get_cases(
    params: GetCasesQuery = Depends(), db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> GetCasesResponse:
    service = CaseService(db)
    try:
        return await service.get_cases(params, current_user.id, current_user.role)
    except Exception as err:
        logger.exception("Error fetching cases list")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Не удалось получить список дел",
        ) from err


@router.post(
    "",
    response_model=CaseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать новое дело",
    description="Регистрирует новое дело в системе",
)
async def create_case(
    case_data: CaseCreateRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> CaseResponse:
    if current_user.role == UserRole.EXPERT:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет прав для создания дел")

    service = CaseService(db)
    try:
        return await service.create_case(case_data, current_user.id, current_user.role)
    except HTTPException:
        raise
    except IntegrityError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Произошла ошибка при сохранении данных",
        ) from err
    except Exception as err:
        logger.exception("Unexpected error during case creation")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Внутренняя ошибка сервера при создании дела",
        ) from err


@router.get(
    "/{case_id}",
    response_model=CaseDetailsResponse,
    summary="Детальная информация о деле",
    description="Возвращает полные данные дела, включая клиента, документы, события и назначенных экспертов.",
)
async def get_case_details(
    case_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CaseDetailsResponse:
    service = CaseService(db)
    case_details = await service.get_case_details(
        case_id=str(case_id),
        user_id=current_user.id,
        user_role=current_user.role,
    )

    if case_details is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Дело не найдено или нет доступа")

    return case_details


@router.patch(
    "/{case_id}",
    response_model=CaseResponse,
    summary="Обновить данные дела",
    description="Частичное обновление информации по существующему делу",
)
async def update_case(
    case_id: UUID, case_data: CaseUpdateRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> CaseResponse:
    service = CaseService(db)
    try:
        result = await service.update_case(str(case_id), case_data, current_user.id, current_user.role)
        if not result:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Дело не найдено")
        return result
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err
    except Exception as err:
        logger.exception(f"Error updating case {case_id}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при обновлении данных дела",
        ) from err


@router.delete(
    "/{case_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить дело",
    description="Выполняет мягкое удаление дела (пометка deleted_at)",
)
async def delete_case(case_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)) -> None:
    if current_user.role == UserRole.EXPERT:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="У вас нет прав для удаления дела")

    service = CaseService(db)
    success = await service.soft_delete_case(str(case_id), current_user.id, current_user.role)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Дело не найдено")
    return None
