from __future__ import annotations

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Path, Query, Response, UploadFile, status
from fastapi import Form as FastAPIForm
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.auth.deps import get_current_user
from src.app.core.auth.models import UserContext
from src.app.core.database.session import get_db
from src.app.core.schemas.base import PaginatedResponse
from src.app.services.mail.models import MailFolder
from src.app.services.mail.schemas import (
    MailAttachmentRead,
    MailListItem,
    MailMessageBulkAction,
    MailMessageBulkResult,
    MailMessageCreate,
    MailMessageRead,
    MailMessageUpdate,
    MailSendResult,
    MailSyncResult,
    MailThreadRead,
    OversizedBatchOut,
    OversizedFileDownloadOut,
    OversizedZipOut,
    PaginatedMailMessages,
    PaginatedMailThread,
)
from src.app.services.mail.service import MailOversizedService, MailService

router = APIRouter(prefix="/api/mail", tags=["Mail"])

MAX_TOTAL_SIZE = 25 * 1024 * 1024
MAX_ATTACHMENTS = 10

_ATTACH_LIMITS = dict(max_total_size=MAX_TOTAL_SIZE, max_count=MAX_ATTACHMENTS)


def _get_service(
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> MailService:
    return MailService(db=db, user=current_user)


_Service = Annotated[MailService, Depends(_get_service)]


def _parse_payload(raw: str) -> MailMessageCreate:
    try:
        return MailMessageCreate.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail=f"Невалидный JSON в поле `data`: {exc}") from exc


@router.get("/threads", response_model=PaginatedResponse[MailListItem])
async def list_threads(
    svc: _Service,
    folder: MailFolder = Query(default=MailFolder.INBOX),
    is_read: bool | None = Query(default=None),
    is_starred: bool | None = Query(default=None),
    is_important: bool | None = Query(default=None),
    case_id: uuid.UUID | None = Query(default=None),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1),
) -> PaginatedResponse[MailListItem]:
    return await svc.list_threads(
        folder=folder,
        is_read=is_read,
        is_important=is_important,
        is_starred=is_starred,
        case_id=case_id,
        search=search,
        page=page,
        page_size=page_size,
    )


# @router.get("/attachments", response_model=PaginatedResponse[MailAttachmentRead])
# async def list_attachments_fiels(
#     svc: _Service,
#     message_id: uuid.UUID = Path(...),
# ) -> PaginatedResponse[MailAttachmentRead]: ...


@router.get("/messages/search", response_model=PaginatedMailMessages)
async def search_messages(
    svc: _Service,
    q: str = Query(min_length=2, max_length=255),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> PaginatedMailMessages:
    return await svc.search_messages(q=q, page=page, page_size=page_size)


@router.get("/messages/{message_id}", response_model=MailMessageRead)
async def get_message(svc: _Service, message_id: uuid.UUID = Path(...)) -> MailMessageRead:
    return await svc.get_message(message_id)


@router.patch("/messages/{message_id}", response_model=MailMessageRead)
async def update_message(
    svc: _Service,
    payload: MailMessageUpdate,
    message_id: uuid.UUID = Path(...),
) -> MailMessageRead:
    return await svc.update_message(message_id, payload)


@router.delete("/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(
    svc: _Service,
    message_id: uuid.UUID = Path(...),
    permanent: bool = Query(default=False),
) -> None:
    await svc.delete_message(message_id, permanent=permanent)


@router.post("/messages/bulk", response_model=MailMessageBulkResult)
async def bulk_action(svc: _Service, payload: MailMessageBulkAction) -> MailMessageBulkResult:
    return await svc.bulk_action(payload)


@router.post("/messages/{message_id}/move", response_model=MailMessageRead)
async def move_message(
    svc: _Service,
    message_id: uuid.UUID = Path(...),
    folder: MailFolder = Query(...),
) -> MailMessageRead:
    return await svc.move_message(message_id, folder)


@router.post("/messages", response_model=MailSendResult, status_code=status.HTTP_201_CREATED)
async def send_message(svc: _Service, payload: MailMessageCreate) -> MailSendResult:
    return await svc.send_message(payload)


@router.post(
    "/messages/with-attachments",
    response_model=MailSendResult,
    status_code=status.HTTP_201_CREATED,
)
async def send_message_with_attachments(
    svc: _Service,
    data: str = FastAPIForm(...),
    files: list[UploadFile] = File(default=[]),
) -> MailSendResult:
    return await svc.send_message_with_attachments(
        payload=_parse_payload(data),
        files=files,
        **_ATTACH_LIMITS,
    )


@router.post(
    "/messages/{message_id}/reply",
    response_model=MailSendResult,
    status_code=status.HTTP_201_CREATED,
)
async def reply_to_message(
    svc: _Service,
    payload: MailMessageCreate,
    message_id: uuid.UUID = Path(...),
    reply_all: bool = Query(default=False),
) -> MailSendResult:
    return await svc.reply_to_message(message_id, payload, reply_all=reply_all)


@router.post(
    "/messages/{message_id}/reply-with-attachments",
    response_model=MailSendResult,
    status_code=status.HTTP_201_CREATED,
)
async def reply_with_attachments(
    svc: _Service,
    message_id: uuid.UUID = Path(...),
    reply_all: bool = Query(default=False),
    data: str = FastAPIForm(...),
    files: list[UploadFile] = File(default=[]),
) -> MailSendResult:
    return await svc.reply_with_attachments(
        message_id,
        _parse_payload(data),
        files,
        reply_all=reply_all,
        **_ATTACH_LIMITS,
    )


@router.post(
    "/messages/{message_id}/forward",
    response_model=MailSendResult,
    status_code=status.HTTP_201_CREATED,
)
async def forward_message(
    svc: _Service,
    payload: MailMessageCreate,
    message_id: uuid.UUID = Path(...),
) -> MailSendResult:
    return await svc.forward_message(message_id, payload)


@router.post(
    "/messages/{message_id}/forward-with-attachments",
    response_model=MailSendResult,
    status_code=status.HTTP_201_CREATED,
)
async def forward_with_attachments(
    svc: _Service,
    message_id: uuid.UUID = Path(...),
    data: str = FastAPIForm(...),
    files: list[UploadFile] = File(default=[]),
) -> MailSendResult:
    return await svc.forward_with_attachments(
        message_id,
        _parse_payload(data),
        files,
        **_ATTACH_LIMITS,
    )


@router.post("/drafts", response_model=MailMessageRead, status_code=status.HTTP_201_CREATED)
async def create_draft(svc: _Service, payload: MailMessageCreate) -> MailMessageRead:
    return await svc.create_draft(payload)


@router.patch("/drafts/{message_id}", response_model=MailMessageRead)
async def update_draft(
    svc: _Service,
    payload: MailMessageCreate,
    message_id: uuid.UUID = Path(...),
) -> MailMessageRead:
    return await svc.update_draft(message_id, payload)


@router.post("/drafts/{message_id}/send", response_model=MailSendResult)
async def send_draft(svc: _Service, message_id: uuid.UUID = Path(...)) -> MailSendResult:
    return await svc.send_draft(message_id)


@router.post("/sync", response_model=MailSyncResult)
async def force_sync(
    svc: _Service,
    folder: MailFolder = Query(default=MailFolder.INBOX),
    days_history: int | None = Query(default=None),
) -> MailSyncResult:
    return await svc.sync_folder(folder=folder, days_history=days_history)


@router.post("/sync/{folder}", response_model=MailSyncResult)
async def sync_folder(svc: _Service, folder: MailFolder = Path(...)) -> MailSyncResult:
    return await svc.sync_folder(folder)


@router.get("/threads/{thread_id}", response_model=MailThreadRead)
async def get_thread(svc: _Service, thread_id: uuid.UUID = Path(...)) -> MailThreadRead:
    return await svc.get_thread(thread_id)


@router.get("/threads/{thread_id}/messages", response_model=PaginatedMailThread)
async def get_thread_paginated(
    svc: _Service,
    thread_id: uuid.UUID = Path(...),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PaginatedMailThread:
    return await svc.get_thread_paginated(thread_id, page=page, page_size=page_size)


@router.get("/messages/{message_id}/attachments", response_model=list[MailAttachmentRead])
async def list_attachments(
    svc: _Service,
    message_id: uuid.UUID = Path(...),
) -> list[MailAttachmentRead]:
    return await svc.get_list_attachments(message_id)


@router.get(
    "/messages/{message_id}/attachments/{attachment_id}/download",
    response_class=RedirectResponse,
    status_code=status.HTTP_302_FOUND,
)
async def download_attachment(
    svc: _Service,
    message_id: uuid.UUID = Path(...),
    attachment_id: uuid.UUID = Path(...),
    download: bool = Query(default=False),
) -> RedirectResponse:
    url = await svc.get_presigned_download_url(message_id, attachment_id, download=download)
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@router.delete("/trash", response_model=dict[str, int])
async def purge_trash(svc: _Service) -> dict[str, int]:
    """Permanently delete all soft-deleted messages and their S3 attachments."""
    purged = await svc.purge_trash()
    return {"purged": purged}


@router.get("/stats", response_model=dict[str, int])
async def get_stats(svc: _Service) -> dict[str, int]:
    return await svc.get_stats()


def _get_service_ovsersized(db: AsyncSession = Depends(get_db)) -> MailOversizedService:
    return MailOversizedService(db)


_ServiceOversized = Annotated[MailOversizedService, Depends(_get_service_ovsersized)]


@router.get(
    "/oversized/{token}",
    response_model=OversizedBatchOut,
    summary="Список файлов по токену",
    description=("Возвращает метаданные всех файлов в батче. Не требует аутентификации — токен сам является ключом доступа."),
)
async def get_batch(
    svc: _ServiceOversized,
    token: str = Path(..., description="Share-токен из ссылки в письме"),
) -> OversizedBatchOut:
    return await svc.get_batch(token)


@router.get(
    "/oversized/{token}/{file_id}/download",
    response_class=RedirectResponse,
    status_code=status.HTTP_302_FOUND,
    summary="Скачать файл",
    description=("Генерирует presigned URL и редиректит на него. Файл отдаётся с Content-Disposition: attachment."),
)
async def download_file(
    svc: _ServiceOversized,
    token: str = Path(...),
    file_id: uuid.UUID = Path(...),
) -> RedirectResponse:
    result = await svc.get_download_url(token, file_id)
    return RedirectResponse(url=result.url, status_code=status.HTTP_302_FOUND)


@router.get(
    "/oversized/{token}/{file_id}/preview",
    response_class=RedirectResponse,
    status_code=status.HTTP_302_FOUND,
    summary="Предпросмотр файла",
    description=(
        "Генерирует presigned URL и редиректит на него. "
        "Файл отдаётся с Content-Disposition: inline — браузер покажет его встроенно "
        "(PDF, изображения, видео). Для остальных типов откроется скачивание."
    ),
)
async def preview_file(
    svc: _ServiceOversized,
    token: str = Path(...),
    file_id: uuid.UUID = Path(...),
) -> RedirectResponse:
    result = await svc.get_preview_url(token, file_id)
    return RedirectResponse(url=result.url, status_code=status.HTTP_302_FOUND)


@router.get(
    "/oversized/{token}/{file_id}/url",
    response_model=OversizedFileDownloadOut,
    summary="Presigned URL для файла (JSON)",
    description=(
        "Возвращает presigned URL в виде JSON. "
        "Используется когда фронтенд хочет самостоятельно управлять загрузкой "
        "(например, показать прогресс-бар или встроить в <video>/<img>)."
    ),
)
async def get_file_url(svc: _ServiceOversized, token: str = Path(...), file_id: uuid.UUID = Path(...)) -> OversizedFileDownloadOut:
    return await svc.get_download_url(token, file_id)


@router.get(
    "/oversized/{token}/{file_id}/preview-url",
    response_model=OversizedFileDownloadOut,
    summary="Presigned URL для предпросмотра (JSON)",
)
async def get_preview_url(svc: _ServiceOversized, token: str = Path(...), file_id: uuid.UUID = Path(...)) -> OversizedFileDownloadOut:
    return await svc.get_preview_url(token, file_id)


@router.get(
    "/oversized/{token}/zip",
    summary="Скачать все файлы одним ZIP-архивом",
    description=(
        "Загружает все файлы из S3, упаковывает в ZIP в памяти и стримит клиенту. "
        "Подходит для батчей до ~200 МБ суммарно. "
        "Не требует аутентификации."
    ),
)
async def download_zip(svc: _ServiceOversized, token: str = Path(...)) -> Response:
    zip_bytes, zip_name = await svc.stream_zip(token)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


@router.get(
    "/oversized/{token}/download-all",
    response_model=OversizedZipOut,
    summary="Получить ссылки на все файлы",
    description=(
        "Возвращает presigned URL для каждого файла в батче. "
        "Используется когда фронтенд хочет скачивать файлы параллельно "
        "или показывать индивидуальный прогресс."
    ),
)
async def get_all_urls(svc: _ServiceOversized, token: str = Path(...)) -> OversizedZipOut:
    return await svc.get_all_download_urls(token)


# @router.get(
#     "/contacts/autocomplete",
#     response_model=EmailContactAutocompleteResponse,
#     summary="Автокомплит email-адресов",
#     description=(
#         "Возвращает список email-адресов по префиксу или части имени. "
#         "Используется при вводе получателя в форме отправки письма. "
#         "Поиск работает по email (префикс) и имени (вхождение). "
#         "Сортировка: наиболее часто используемые — первыми."
#     ),
# )
# async def autocomplete_contacts(
#     svc: _Service,
#     q: str = Query(min_length=1, max_length=100, description="Строка поиска: начало email или часть имени"),
#     limit: int = Query(default=10, ge=1, le=50, description="Максимальное количество результатов"),
# ) -> EmailContactAutocompleteResponse:
#     return await svc.autocomplete(q=q, limit=limit)
