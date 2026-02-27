import io
import logging
import re
import urllib.parse
import uuid
import zipfile
from collections.abc import AsyncGenerator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.auth.deps import UserContext, get_current_user
from src.app.core.database.session import get_db
from src.app.services.case.models import Case
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
    params: GetCasesQuery = Depends(), db: AsyncSession = Depends(get_db), current_user: UserContext = Depends(get_current_user)
) -> GetCasesResponse:
    service = CaseService(db)
    return await service.get_cases(params, current_user.id, current_user.role, current_user.company_id)


@router.post("", response_model=CaseResponse, status_code=status.HTTP_201_CREATED)
async def create_case(
    case_data: CaseCreateRequest, db: AsyncSession = Depends(get_db), current_user: UserContext = Depends(get_current_user)
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
    details = await service.get_case_details(case_id, current_user.id, current_user.role, current_user.company_id)
    return details


@router.patch("/{case_id}", response_model=CaseResponse)
async def update_case(
    case_id: UUID, case_data: CaseUpdateRequest, db: AsyncSession = Depends(get_db), current_user: UserContext = Depends(get_current_user)
) -> CaseResponse:
    service = CaseService(db)
    result = await service.update_case(case_id, case_data, current_user.role, current_user.company_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Дело не найдено")
    return result


@router.delete("/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(case_id: UUID, db: AsyncSession = Depends(get_db), current_user: UserContext = Depends(get_current_user)) -> None:
    service = CaseService(db)
    if not await service.soft_delete_case(case_id, current_user.role, current_user.company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Дело не найдено")
