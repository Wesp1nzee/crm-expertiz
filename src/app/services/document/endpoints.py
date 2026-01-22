import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.database import get_db
from src.app.services.document.schemas import DocumentDownloadUrl, DocumentResponse
from src.app.services.document.service import DocumentService

router = APIRouter(prefix="/api/documents", tags=["Documents"])


@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Загрузить новый документ",
    description="Принимает файл, загружает его в S3 хранилище и сохраняет метаданные в базе данных.",
)
async def upload_document(
    file: UploadFile = File(...),
    case_id: uuid.UUID | None = Form(None, description="ID дела, к которому привязан документ"),
    title: str | None = Form(None, description="Название документа (по умолчанию берется имя файла)"),
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    service = DocumentService(db)
    doc = await service.upload_document(file=file, case_id=case_id, title=title)
    return DocumentResponse.model_validate(doc)


@router.get(
    "",
    response_model=list[DocumentResponse],
    status_code=status.HTTP_200_OK,
    summary="Получить список документов",
    description="Возвращает список документов с поддержкой поиска, сортировки и пагинации.",
)
async def list_documents(
    case_id: uuid.UUID | None = Query(None, description="Фильтр по ID дела"),
    search: str | None = Query(None, description="Поиск по названию документа"),
    sort_by: str = Query("created_at", description="Поле для сортировки (created_at, title, file_size)"),
    order: str = Query("desc", regex="^(asc|desc)$", description="Направление сортировки"),
    limit: int = Query(20, ge=1, le=100, description="Количество записей на страницу"),
    offset: int = Query(0, ge=0, description="Смещение (пропуск записей)"),
    db: AsyncSession = Depends(get_db),
) -> list[DocumentResponse]:
    service = DocumentService(db)
    docs = await service.list_documents(
        case_id=case_id,
        search=search,
        sort_by=sort_by,
        order=order,
        limit=limit,
        offset=offset,
    )
    return [DocumentResponse.model_validate(d) for d in docs]


@router.get(
    "/{doc_id}/download",
    response_model=DocumentDownloadUrl,
    status_code=status.HTTP_200_OK,
    summary="Получить ссылку на скачивание",
    description="Генерирует временную (presigned) URL-ссылку для безопасного скачивания файла из S3.",
    responses={404: {"description": "Документ не найден"}},
)
async def get_document_url(doc_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> DocumentDownloadUrl:
    service = DocumentService(db)
    url = await service.get_presigned_url(doc_id)
    if not url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Документ не найден")

    return DocumentDownloadUrl(download_url=url)


@router.delete(
    "/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить документ",
    description="Удаляет запись о документе из БД и соответствующий объект из S3 хранилища.",
    responses={404: {"description": "Документ не найден"}},
)
async def delete_document(doc_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    service = DocumentService(db)
    if not await service.delete_document(doc_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Документ не найден")
    return None
