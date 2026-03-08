import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.auth.deps import get_current_user
from src.app.core.auth.models import UserContext
from src.app.core.database.session import get_db
from src.app.services.document.models import ShareType
from src.app.services.share.schemas import (
    AccessLinkSchema,
    CreateLinkShareSchema,
    CreateUserShareSchema,
    ResourceShareInfoOut,
    ShareAccessLinkResult,
    ShareBatchOut,
)
from src.app.services.share.service import ShareService

router = APIRouter(prefix="/api/share", tags=["Share"])


@router.post(
    "/link",
    response_model=ShareBatchOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создать публичную ссылку",
    description=(
        "Создаёт публичную ссылку на один или несколько документов/папок. "
        "Ссылка может быть защищена паролем и/или ограничена по времени. "
        "Недоступно для роли EXPERT."
    ),
)
async def create_link_share(
    data: CreateLinkShareSchema,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> ShareBatchOut:
    service = ShareService(db)
    return await service.create_link_share(current_user=current_user, data=data)


@router.post(
    "/user",
    response_model=list[ShareBatchOut],
    status_code=status.HTTP_201_CREATED,
    summary="Передать файлы сотрудникам",
    description=(
        "Предоставляет доступ к документам/папкам одному или нескольким сотрудникам компании. "
        "Для каждого получателя создаётся отдельный батч — доступ можно отозвать у одного, "
        "не затрагивая остальных. Недоступно для роли EXPERT."
    ),
)
async def create_user_share(
    data: CreateUserShareSchema,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> list[ShareBatchOut]:
    service = ShareService(db)
    return await service.create_user_share(current_user=current_user, data=data)


@router.get(
    "/resource",
    response_model=ResourceShareInfoOut,
    status_code=status.HTTP_200_OK,
    summary="Кому передан файл или папка",
    description=(
        "Возвращает список сотрудников, которым передан ресурс, "
        "и список активных публичных ссылок на него. "
        "Передать нужно ровно один из параметров: document_id или folder_id."
    ),
)
async def get_resource_shares(
    document_id: uuid.UUID | None = Query(None, description="ID документа"),
    folder_id: uuid.UUID | None = Query(None, description="ID папки"),
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> ResourceShareInfoOut:
    service = ShareService(db)
    return await service.get_resource_shares(
        current_user=current_user,
        document_id=document_id,
        folder_id=folder_id,
    )


@router.post(
    "/access/{token}",
    response_model=ShareAccessLinkResult,
    status_code=status.HTTP_200_OK,
    summary="Открыть публичную ссылку",
    description=(
        "Валидирует токен публичной ссылки, проверяет пароль (если задан) и срок действия. Логирует просмотр. Не требует аутентификации."
    ),
)
async def access_link(
    token: str,
    request: Request,
    password: str | None = Query(None, description="Пароль (если ссылка защищена)"),
    db: AsyncSession = Depends(get_db),
) -> ShareAccessLinkResult:
    service = ShareService(db)
    schema = AccessLinkSchema(
        token=token,
        password=password,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return await service.access_link(schema=schema)


@router.post(
    "/access/{token}/download",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Залогировать скачивание по публичной ссылке",
    description=(
        "Фиксирует факт скачивания файла по публичной ссылке в журнале аудита. "
        "Вызывается фронтендом в момент нажатия кнопки «Скачать». "
        "Не требует аутентификации."
    ),
)
async def log_download(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> None:
    service = ShareService(db)
    await service.log_download(
        token=token,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


@router.get(
    "/my",
    response_model=list[ShareBatchOut],
    status_code=status.HTTP_200_OK,
    summary="Мои шары",
    description="Возвращает все шары, созданные текущим пользователем.",
)
async def get_my_shares(
    share_type: ShareType | None = Query(None, description="Фильтр по типу: link / user"),
    only_active: bool = Query(True, description="Показывать только активные"),
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> list[ShareBatchOut]:
    service = ShareService(db)
    return await service.get_my_shares(
        current_user=current_user,
        share_type=share_type,
        only_active=only_active,
    )


@router.get(
    "/inbox",
    response_model=list[ShareBatchOut],
    status_code=status.HTTP_200_OK,
    summary="Доступно мне",
    description="Возвращает все USER-шары, переданные текущему пользователю другими сотрудниками.",
)
async def get_shared_with_me(
    only_active: bool = Query(True, description="Показывать только активные"),
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> list[ShareBatchOut]:
    service = ShareService(db)
    return await service.get_shared_with_me(current_user=current_user, only_active=only_active)


@router.get(
    "/{batch_id}",
    response_model=ShareBatchOut,
    status_code=status.HTTP_200_OK,
    summary="Детали шара",
    description=(
        "Возвращает полные данные батча, включая счётчики просмотров и скачиваний. "
        "Доступно владельцу, получателю или пользователям с ролью ADMIN / CEO."
    ),
)
async def get_batch_detail(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> ShareBatchOut:
    service = ShareService(db)
    return await service.get_batch_detail(current_user=current_user, batch_id=batch_id)


@router.delete(
    "/{batch_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Отозвать доступ",
    description=(
        "Деактивирует шар — ссылка перестаёт работать, сотрудник теряет доступ. Доступно владельцу шара или пользователям с ролью ADMIN / CEO."
    ),
)
async def revoke_share(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: UserContext = Depends(get_current_user),
) -> None:
    service = ShareService(db)
    await service.revoke_share(current_user=current_user, batch_id=batch_id)
