import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.database.session import get_db
from src.app.services.document.models import Folder
from src.app.services.document.schemas import DocumentDownloadUrl, DocumentResponse, FileSystemEntry, FolderCreate, FolderResponse
from src.app.services.document.service import DocumentService

router = APIRouter(prefix="/api/documents", tags=["Documents"])


@router.get(
    "",
    response_model=list[FileSystemEntry],
    status_code=status.HTTP_200_OK,
    summary="Получить список файлов и папок",
    description=(
        "Возвращает объединенный список папок и файлов. Если передан search, ищет глобально. Если нет - показывает содержимое конкретной папки."
    ),
)
async def list_assets(
    folder_id: uuid.UUID | None = Query(None, description="ID папки (null для корня)"),
    case_id: uuid.UUID | None = Query(None, description="Фильтр по конкретному делу"),
    search: str | None = Query(None, description="Поиск по названию"),
    sort_by: str = Query("created_at", description="Поле для сортировки (name, created_at, size)"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[FileSystemEntry]:
    return await DocumentService(db).get_unified_list(
        folder_id=folder_id, case_id=case_id, search=search, sort_by=sort_by, order=order, limit=limit, offset=offset
    )


@router.post(
    "/folders",
    response_model=FolderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать папку",
)
async def create_folder(
    folder_data: FolderCreate,
    db: AsyncSession = Depends(get_db),
) -> FolderResponse:
    service = DocumentService(db)
    result = await service.create_folder(folder_data, user_id=None)
    return FolderResponse.model_validate(result)


@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Загрузить документ",
)
async def upload_document(
    file: UploadFile = File(...),
    case_id: uuid.UUID | None = Form(None),
    folder_id: uuid.UUID | None = Form(None),
    title: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    service = DocumentService(db)
    result = await service.upload_document(file=file, case_id=case_id, folder_id=folder_id, title=title, user_id=None)
    return DocumentResponse.model_validate(result)


@router.get(
    "/{document_id}/url",
    summary="Получить ссылку на скачивание",
)
async def get_document_url(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> DocumentDownloadUrl:
    service = DocumentService(db)
    url = await service.get_presigned_url(document_id)
    if not url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Документ не найден")
    return DocumentDownloadUrl.model_validate(url)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить документ",
)
async def delete_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    service = DocumentService(db)
    success = await service.delete_document(document_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Документ не найден")


@router.delete(
    "/folders/{folder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить папку",
)
async def delete_folder(
    folder_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    await db.execute(delete(Folder).where(Folder.id == folder_id))
    await db.commit()
