import io
import urllib.parse
import uuid
import zipfile
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.auth.deps import get_current_user
from src.app.core.database.session import get_db
from src.app.services.document.models import Folder
from src.app.services.document.schemas import (
    AssetUpdate,
    DocumentDownloadUrl,
    DocumentResponse,
    DocumentUpdate,
    EntryType,
    FileSystemEntry,
    FolderCreate,
    FolderResponse,
    FolderUpdate,
)
from src.app.services.document.service import DocumentService
from src.app.services.user.models import User

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
    current_user: User = Depends(get_current_user),
) -> list[FileSystemEntry]:
    service = DocumentService(db)
    return await service.get_unified_list(
        folder_id=folder_id,
        case_id=case_id,
        search=search,
        sort_by=sort_by,
        order=order,
        limit=limit,
        offset=offset,
        user_id=current_user.id,
        user_role=current_user.role,
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
    current_user: User = Depends(get_current_user),
) -> FolderResponse:
    service = DocumentService(db)
    result = await service.create_folder(folder_data, current_user.id, current_user.role)
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
    current_user: User = Depends(get_current_user),
) -> DocumentResponse:
    service = DocumentService(db)
    result = await service.upload_document(file=file, user_id=current_user.id, case_id=case_id, folder_id=folder_id, title=title)
    return DocumentResponse.model_validate(result)


@router.get("/{document_id}/url", summary="Получить ссылку на документ", response_description="Ссылка для просмотра или скачивания документа")
async def get_document_url(
    document_id: uuid.UUID,
    download: bool = Query(default=False, description="Режим скачивания. Если True - файл скачивается, если False - открывается в браузере"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DocumentDownloadUrl:
    """
    Получить временную ссылку для доступа к документу.
    """
    service = DocumentService(db)
    url = await service.get_presigned_url(document_id, download=download)
    if not url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Документ не найден")
    return DocumentDownloadUrl(download_url=url)


@router.get("/folders/{folder_id}/download", summary="Скачать папку как ZIP-архив")
async def download_folder_as_zip(
    folder_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """
    Скачивание всей папки как ZIP-архива.
    """
    service = DocumentService(db)

    # Проверяем наличие папки до создания архива
    folder_result = await db.execute(select(Folder).where(Folder.id == folder_id))
    folder = folder_result.scalar_one_or_none()
    if not folder:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Папка не найдена")

    async def generate_zip() -> AsyncGenerator[bytes]:
        buffer = io.BytesIO()

        # Создаем ZIP-архив в памяти
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            await service.add_folder_to_zip(zip_file, folder_id, "", current_user.id, current_user.role)

        buffer.seek(0)
        while True:
            chunk = buffer.read(8192)
            if not chunk:
                break
            yield chunk

    # Очищаем имя файла от потенциально опасных символов
    safe_folder_name = folder.name.replace('"', "").replace("'", "").replace(";", "").replace(",", "")

    encoded_filename = urllib.parse.quote(safe_folder_name, safe="")

    return StreamingResponse(
        generate_zip(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{encoded_filename}.zip"'}
    )


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить документ",
)
async def delete_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
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
    current_user: User = Depends(get_current_user),
) -> None:
    await db.execute(delete(Folder).where(Folder.id == folder_id))
    await db.commit()


@router.patch(
    "/update",
    summary="Обновить файл или папку",
    description="Единый эндпоинт для обновления документов и папок. Можно изменить имя, переместить в другую папку или изменить привязку к делу.",
)
async def update_asset(
    asset_data: AssetUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DocumentResponse | FolderResponse:
    service = DocumentService(db)

    print(f"Asset  {asset_data}")

    if asset_data.asset_type == EntryType.FILE:
        if not isinstance(asset_data.data, DocumentUpdate):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Для файлов данные должны быть типа DocumentUpdate")

        update_dict = asset_data.data.model_dump(exclude_unset=True)
        document = await service.update_document(
            document_id=asset_data.asset_id, update_data=update_dict, user_id=current_user.id, user_role=current_user.role
        )

        if not document:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Документ не найден")

        return DocumentResponse.model_validate(document)

    else:  # EntryType.FOLDER
        if not isinstance(asset_data.data, FolderUpdate):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Для папок данные должны быть типа FolderUpdate")

        update_dict = asset_data.data.model_dump(exclude_unset=True)
        folder = await service.update_folder(
            folder_id=asset_data.asset_id, update_data=update_dict, user_id=current_user.id, user_role=current_user.role
        )

        if not folder:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Папка не найдена",
            )

        return FolderResponse.model_validate(folder)
