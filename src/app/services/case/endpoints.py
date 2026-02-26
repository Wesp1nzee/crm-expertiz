import io
import logging
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
from src.app.core.storage.s3 import s3_storage
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
from src.app.services.document.models import Document
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


@router.get("/{case_id}/download-documents")
async def download_case_documents_as_zip(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> StreamingResponse:
    case_result = await db.execute(select(Case).where(Case.id == case_id, Case.company_id == current_user.company_id))
    case = case_result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Дело не найдено")

    service = DocumentService(db)

    async def generate_zip() -> AsyncGenerator[bytes]:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            documents_result = await db.execute(select(Document).where(Document.case_id == case_id))
            documents = documents_result.scalars().all()
            for doc in documents:
                if await service._check_document_access(doc, current_user.id, current_user.role, current_user.company_id):
                    file_content = await s3_storage.get_file_content(doc.file_path)
                    zip_file.writestr(doc.title.replace('"', ""), file_content)
        buffer.seek(0)
        yield buffer.read()

    filename = f"case_{case.number}_documents.zip"
    return StreamingResponse(generate_zip(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


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
