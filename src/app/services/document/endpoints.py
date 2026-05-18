import io
import urllib.parse
import uuid
import zipfile
from collections.abc import AsyncGenerator
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.auth.deps import get_current_user
from src.app.core.auth.models import UserContext
from src.app.core.database.session import get_db
from src.app.core.schemas.base import PaginatedResponse
from src.app.services.document.models import Folder
from src.app.services.document.schemas import (
    AssetUpdate,
    BulkDownloadRequest,
    DocumentDownloadUrl,
    DocumentResponse,
    DocumentsBulkDeleteRequest,
    DocumentUpdate,
    EntryType,
    FileSystemEntry,
    FolderCreate,
    FolderResponse,
    FolderUpdate,
    RestoreOperationResponse,
    TrashOperationResponse,
)
from src.app.services.document.service import DocumentService

router = APIRouter(prefix="/api/documents", tags=["Documents"])


@router.get(
    "",
    response_model=PaginatedResponse[FileSystemEntry],
    status_code=status.HTTP_200_OK,
    summary="Получить список файлов и папок",
    description=(
        "Возвращает объединённый список папок и файлов с пагинацией. "
        "По умолчанию показывает только файлы и папки пользователя. "
        "Параметр `scope` позволяет переключаться между своими и общими файлами (только для CEO и ACCOUNTANT). "
        "Если передан search — ищет глобально, иначе показывает содержимое папки."
    ),
)
async def list_assets(
    request: Request,
    folder_id: uuid.UUID | None = Query(None, description="ID папки (null для корня)"),
    case_id: uuid.UUID | None = Query(None, description="Фильтр по конкретному делу"),
    search: str | None = Query(None, description="Поиск по названию"),
    sort_by: str = Query("created_at", description="Поле сортировки: name, created_at, size"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    scope: str = Query(
        "my", pattern="^(my|all)$", description="Область просмотра: my — свои файлы, all — все файлы компании (только CEO/ACCOUNTANT)"
    ),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> PaginatedResponse[FileSystemEntry]:
    service = DocumentService(db)

    response = await service.get_unified_list(
        company_id=current_user.company_id,
        page=page,
        limit=limit,
        folder_id=folder_id,
        case_id=case_id,
        search=search,
        sort_by=sort_by,
        order=order,
        user_id=current_user.id,
        user_role=current_user.role,
        scope=scope,
    )

    path = request.url.path

    def build_relative_link(p: int) -> str:
        params: dict[str, str] = dict(request.query_params)
        params.update({"page": str(p), "limit": str(limit)})
        return f"{path}?{urlencode(params)}"

    if response.meta.has_next:
        response.meta.next_page_url = build_relative_link(page + 1)
    if response.meta.has_prev:
        response.meta.prev_page_url = build_relative_link(page - 1)

    return response


@router.post(
    "/folders",
    response_model=FolderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать папку",
)
async def create_folder(
    folder_data: FolderCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> FolderResponse:
    service = DocumentService(db)
    result = await service.create_folder(
        folder_data=folder_data,
        user_id=current_user.id,
        company_id=current_user.company_id,
    )
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
    current_user: UserContext = Depends(get_current_user),
) -> DocumentResponse:
    service = DocumentService(db)
    result = await service.upload_document(
        file=file,
        user_id=current_user.id,
        company_id=current_user.company_id,
        case_id=case_id,
        folder_id=folder_id,
        title=title,
    )
    return DocumentResponse.model_validate(result)


@router.delete(
    "/bulk",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Массовое удаление файлов и папок",
    description="Удаляет список документов и папок. При удалении папки удаляются все вложенные объекты.",
)
async def delete_documents_bulk(
    request: DocumentsBulkDeleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> None:
    if not request.folder_ids and not request.document_ids:
        return

    service = DocumentService(db)
    await service.delete_bulk(
        folder_ids=request.folder_ids,
        document_ids=request.document_ids,
        company_id=current_user.company_id,
    )


@router.post("/download-bulk", summary="Массовое скачивание документов и папок")
async def download_bulk(
    request: BulkDownloadRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> StreamingResponse:
    if not request.folder_ids and not request.document_ids:
        raise HTTPException(status_code=400, detail="Ничего не выбрано для скачивания")

    service = DocumentService(db)

    async def generate_zip() -> AsyncGenerator[bytes]:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            await service.download_bulk_to_zip(
                zip_file=zip_file,
                folder_ids=request.folder_ids,
                document_ids=request.document_ids,
                user_id=current_user.id,
                user_role=current_user.role,
                company_id=current_user.company_id,
            )

        buffer.seek(0)
        while chunk := buffer.read(1024 * 64):
            yield chunk
        buffer.close()

    filename = urllib.parse.quote(f"export_{uuid.uuid4().hex[:8]}.zip")

    return StreamingResponse(
        generate_zip(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.patch(
    "/update",
    summary="Обновить файл или папку",
    description="Единый эндпоинт для обновления документов и папок. Можно изменить имя, переместить в другую папку или изменить привязку к делу.",
)
async def update_asset(
    asset_data: AssetUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> DocumentResponse | FolderResponse:
    service = DocumentService(db)

    if asset_data.asset_type == EntryType.FILE:
        if not isinstance(asset_data.data, DocumentUpdate):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Для файлов данные должны быть типа DocumentUpdate")

        update_dict = asset_data.data.model_dump(exclude_unset=True)
        document = await service.update_document(
            document_id=asset_data.asset_id,
            update_data=update_dict,
            user_id=current_user.id,
            user_role=current_user.role,
            company_id=current_user.company_id,
        )

        if not document:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Документ не найден")

        return DocumentResponse.model_validate(document)

    else:  # EntryType.FOLDER
        if not isinstance(asset_data.data, FolderUpdate):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Для папок данные должны быть типа FolderUpdate")

        update_dict = asset_data.data.model_dump(exclude_unset=True)
        folder = await service.update_folder(
            folder_id=asset_data.asset_id,
            update_data=update_dict,
            user_id=current_user.id,
            user_role=current_user.role,
            company_id=current_user.company_id,
        )

        if not folder:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Папка не найдена",
            )

        return FolderResponse.model_validate(folder)


@router.get("/folders/{folder_id}/download", summary="Скачать папку как ZIP-архив")
async def download_folder_as_zip(
    folder_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> StreamingResponse:
    """
    Скачивание всей папки как ZIP-архива.
    """
    service = DocumentService(db)

    folder_result = await db.execute(
        select(Folder).where(
            Folder.id == folder_id,
            Folder.company_id == current_user.company_id,
        )
    )
    folder = folder_result.scalar_one_or_none()
    if not folder:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Папка не найдена")

    async def generate_zip() -> AsyncGenerator[bytes]:
        buffer = io.BytesIO()

        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            await service.add_folder_to_zip(
                zip_file=zip_file,
                folder_id=folder_id,
                path_prefix="",
                user_id=current_user.id,
                user_role=current_user.role,
                company_id=current_user.company_id,
            )

        buffer.seek(0)
        while True:
            chunk = buffer.read(8192)
            if not chunk:
                break
            yield chunk

    safe_folder_name = folder.name.replace('"', "").replace("'", "").replace(";", "").replace(",", "")
    encoded_filename = urllib.parse.quote(safe_folder_name, safe="")

    return StreamingResponse(
        generate_zip(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{encoded_filename}.zip"'},
    )


@router.delete(
    "/folders/{folder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить папку",
)
async def delete_folder(
    folder_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> None:
    service = DocumentService(db)
    await service.delete_folder(
        folder_id=folder_id,
        company_id=current_user.company_id,
    )


@router.get(
    "/{document_id}/url",
    summary="Получить ссылку на документ",
    response_description="Ссылка для просмотра или скачивания документа",
)
async def get_document_url(
    document_id: uuid.UUID,
    download: bool = Query(default=False, description="Режим скачивания. Если True - файл скачивается, если False - открывается в браузере"),
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> DocumentDownloadUrl:
    """
    Получить временную ссылку для доступа к документу.
    """
    service = DocumentService(db)
    url = await service.get_presigned_url(
        doc_id=document_id,
        company_id=current_user.company_id,
        download=download,
    )
    if not url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Документ не найден")
    return DocumentDownloadUrl(download_url=url)


@router.post("/trash", status_code=status.HTTP_200_OK, summary="Переместить в корзину", response_model=TrashOperationResponse)
async def move_to_trash(
    request: DocumentsBulkDeleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> TrashOperationResponse:
    service = DocumentService(db)
    result = await service.move_to_trash(
        folder_ids=request.folder_ids,
        document_ids=request.document_ids,
        user_id=current_user.id,
        company_id=current_user.company_id,
    )
    return TrashOperationResponse(message="Элементы перемещены в корзину", moved=result)


@router.post("/restore", status_code=status.HTTP_200_OK, summary="Восстановить из корзины", response_model=RestoreOperationResponse)
async def restore_from_trash(
    request: DocumentsBulkDeleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> RestoreOperationResponse:
    service = DocumentService(db)
    result = await service.restore_from_trash(
        folder_ids=request.folder_ids,
        document_ids=request.document_ids,
        user_id=current_user.id,
        company_id=current_user.company_id,
    )
    return RestoreOperationResponse(message="Элементы восстановлены", restored=result)


@router.delete("/trash", status_code=status.HTTP_204_NO_CONTENT, summary="Безвозвратное удаление документов и папок из корзины")
async def permanently_delete_from_trash(
    request: DocumentsBulkDeleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> None:
    service = DocumentService(db)
    await service.permanently_delete(
        folder_ids=request.folder_ids,
        document_ids=request.document_ids,
        company_id=current_user.company_id,
    )


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить документ",
)
async def delete_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> None:
    service = DocumentService(db)

    success = await service.delete_document(
        doc_id=document_id,
        company_id=current_user.company_id,
    )
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Документ не найден")


@router.get(
    "/trash",
    response_model=PaginatedResponse[FileSystemEntry],
    status_code=status.HTTP_200_OK,
    summary="Просмотр корзины",
    description="Показывает удалённые документы и папки. Можно восстановить или удалить безвозвратно.",
)
async def list_trash(
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> PaginatedResponse[FileSystemEntry]:
    service = DocumentService(db)
    return await service.get_trash_items(
        company_id=current_user.company_id,
        user_id=current_user.id,
        user_role=current_user.role,
        page=page,
        limit=limit,
    )
