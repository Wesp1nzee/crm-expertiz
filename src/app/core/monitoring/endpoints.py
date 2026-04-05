from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.database.session import get_db
from src.app.core.monitoring.health import HealthCheckService

router = APIRouter(tags=["Health Checks"])


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """
    Базовая проверка здоровья приложения.
    Возвращает статус 200 если приложение живо.
    """
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "ok"},
    )


@router.get("/ready")
async def readiness_check(db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """
    Проверка готовности приложения к обработке запросов.
    Проверяет подключение к PostgreSQL, Redis и S3.
    """
    health_status = await HealthCheckService.check_all(db)

    if health_status.status == "ok":
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ready", "details": health_status.details},
        )
    else:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "details": health_status.details},
        )
