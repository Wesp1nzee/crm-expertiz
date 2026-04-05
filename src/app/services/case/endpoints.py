import io
import logging
import re
import urllib.parse
import uuid
import zipfile
from collections.abc import AsyncGenerator
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.auth.deps import get_current_user
from src.app.core.auth.models import UserContext
from src.app.core.database.session import get_db
from src.app.services.case.export_service import CaseExcelExportService
from src.app.services.case.models import Case, CaseStatus
from src.app.services.case.schemas import (
    AssignExpertsRequest,
    CaseCreateRequest,
    CaseDetailsResponse,
    CaseResponse,
    CaseSuggestionResponse,
    CaseUpdateRequest,
    FinancialSummaryResponse,
    GetCasesQuery,
    GetCasesResponse,
    SortField,
    SortOrder,
)
from src.app.services.case.service import CaseService
from src.app.services.document.service import DocumentService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cases", tags=["Cases"])


@router.get("/financial-summary", response_model=FinancialSummaryResponse)
async def get_financial_summary(
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> FinancialSummaryResponse:
    service = CaseService(db)
    return await service.get_financial_summary(current_user.id, current_user.role, current_user.company_id)


@router.get("/suggest", response_model=list[CaseSuggestionResponse])
async def suggest_case(
    q: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> list[CaseSuggestionResponse]:
    service = CaseService(db)
    return await service.suggest_cases(q, current_user.id, current_user.role, current_user.company_id)


@router.get("", response_model=GetCasesResponse)
async def get_cases(
    status_raw: str = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    sort_field: SortField | None = SortField.CREATED_AT,
    sort_order: SortOrder | None = SortOrder.DESC,
    expert_id: uuid.UUID | None = None,
    client_id: uuid.UUID | None = None,
    case_type: str | None = None,
    object_type: str | None = None,
    authority: str | None = None,
    object_address: str | None = None,
    number: str | None = None,
    case_number: str | None = None,
    search: str | None = None,
    min_cost: Decimal | None = None,
    max_cost: Decimal | None = None,
    min_remaining_debt: Decimal | None = None,
    max_remaining_debt: Decimal | None = None,
    completion_start_date: datetime | None = None,
    completion_end_date: datetime | None = None,
    deadline_start_date: datetime | None = None,
    deadline_end_date: datetime | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> GetCasesResponse:
    status_list: list[CaseStatus] | None = None
    if status_raw:
        status_list = [CaseStatus(s.strip()) for s in status_raw.split(",") if s.strip()]

    params = GetCasesQuery(
        status=status_list,
        page=page,
        limit=limit,
        sort_field=sort_field,
        sort_order=sort_order,
        expert_id=expert_id,
        client_id=client_id,
        case_type=case_type,
        object_type=object_type,
        authority=authority,
        object_address=object_address,
        number=number,
        case_number=case_number,
        search=search,
        min_cost=min_cost,
        max_cost=max_cost,
        min_remaining_debt=min_remaining_debt,
        max_remaining_debt=max_remaining_debt,
        completion_start_date=completion_start_date,
        completion_end_date=completion_end_date,
        deadline_start_date=deadline_start_date,
        deadline_end_date=deadline_end_date,
        start_date=start_date,
        end_date=end_date,
    )
    service = CaseService(db)
    return await service.get_cases(params, current_user.id, current_user.role, current_user.company_id)


@router.post("", response_model=CaseResponse, status_code=status.HTTP_201_CREATED)
async def create_case(
    case_data: CaseCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> CaseResponse:
    service = CaseService(db)
    return await service.create_case(case_data, current_user.id, current_user.role, current_user.company_id)


@router.get("/{case_id}", response_model=CaseDetailsResponse)
async def get_case_details(
    case_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> CaseDetailsResponse:
    service = CaseService(db)
    return await service.get_case_details(case_id, current_user.id, current_user.role, current_user.company_id)


@router.patch("/{case_id}", response_model=CaseResponse)
async def update_case(
    case_id: UUID,
    case_data: CaseUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> CaseResponse:
    service = CaseService(db)
    result = await service.update_case(case_id, case_data, current_user.role, current_user.company_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Дело не найдено")
    return result


@router.put("/{case_id}/experts", response_model=CaseResponse)
async def assign_experts(
    case_id: UUID,
    data: AssignExpertsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> CaseResponse:
    """Назначить/заменить список экспертов на дело. Передай пустой список чтобы убрать всех."""
    service = CaseService(db)
    return await service.assign_experts(case_id, data, current_user.role, current_user.company_id)


@router.delete("/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(
    case_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> None:
    service = CaseService(db)
    if not await service.soft_delete_case(case_id, current_user.role, current_user.company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Дело не найдено")


@router.get("/{case_id}/download-documents", summary="Скачать все документы дела")
async def download_case_documents_as_zip(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> StreamingResponse:
    case_result = await db.execute(select(Case).where(Case.id == case_id, Case.company_id == current_user.company_id))
    case = case_result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Дело не найдено")

    if not case.root_folder_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="У дела отсутствует корневая папка")

    root_id: uuid.UUID = case.root_folder_id
    service = DocumentService(db)

    async def generate_zip() -> AsyncGenerator[bytes]:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            await service.add_folder_to_zip(
                zip_file=zip_file,
                folder_id=root_id,
                path_prefix="",
                user_id=current_user.id,
                user_role=current_user.role,
                company_id=current_user.company_id,
            )
        buffer.seek(0)
        while True:
            chunk = buffer.read(1024 * 64)
            if not chunk:
                break
            yield chunk
        buffer.close()

    safe_case_name = re.sub(r"[^\w\-. ]", "_", f"case_{case.number}")
    encoded_filename = urllib.parse.quote(safe_case_name)

    return StreamingResponse(
        generate_zip(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{encoded_filename}.zip"'},
    )


@router.get("/export/excel", summary="Экспорт всех дел в Excel")
async def export_cases_to_excel(
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> StreamingResponse:
    """Export all cases to Excel file. High-performance endpoint optimized for large datasets."""
    export_service = CaseExcelExportService(db)

    excel_data = await export_service.export_cases_to_excel(
        user_id=current_user.id,
        user_role=current_user.role,
        company_id=current_user.company_id,
    )

    filename = f"cases_export_{current_user.company_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    encoded_filename = urllib.parse.quote(filename)

    return StreamingResponse(
        io.BytesIO(excel_data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{encoded_filename}"'},
    )
