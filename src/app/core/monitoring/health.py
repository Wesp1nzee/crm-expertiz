from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.redis.redis import get_redis_client
from src.app.core.storage.s3 import s3_storage

logger = logging.getLogger(__name__)


@dataclass
class HealthStatus:
    """Статус компонента системы"""

    status: str = "ok"
    details: dict[str, str] = field(default_factory=dict)


class HealthCheckService:
    """Сервис проверки состояния компонентов системы"""

    @staticmethod
    async def check_postgresql(session: AsyncSession) -> HealthStatus:
        """Проверка подключения к PostgreSQL"""
        try:
            await session.execute(text("SELECT 1"))
            return HealthStatus(status="ok", details={"database": "connected"})
        except Exception as e:
            logger.error(f"PostgreSQL health check failed: {e}")
            return HealthStatus(status="error", details={"database": f"connection failed: {e}"})

    @staticmethod
    async def check_redis() -> HealthStatus:
        """Проверка подключения к Redis"""
        try:
            redis_client = await get_redis_client()
            await redis_client.ping()  # type: ignore[misc]
            return HealthStatus(status="ok", details={"redis": "connected"})
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            return HealthStatus(status="error", details={"redis": f"connection failed: {e}"})

    @staticmethod
    async def check_s3() -> HealthStatus:
        """Проверка подключения к S3"""
        try:
            await s3_storage.client.head_bucket(Bucket=s3_storage.bucket)
            return HealthStatus(status="ok", details={"s3": "connected"})
        except Exception as e:
            logger.error(f"S3 health check failed: {e}")
            return HealthStatus(status="error", details={"s3": f"connection failed: {e}"})

    @staticmethod
    async def check_all(db_session: AsyncSession) -> HealthStatus:
        """Комплексная проверка всех компонентов"""
        db_status = await HealthCheckService.check_postgresql(db_session)
        redis_status = await HealthCheckService.check_redis()
        s3_status = await HealthCheckService.check_s3()

        all_ok = all(s.status == "ok" for s in [db_status, redis_status, s3_status])

        combined_details: dict[str, str] = {}
        combined_details.update(db_status.details)
        combined_details.update(redis_status.details)
        combined_details.update(s3_status.details)

        return HealthStatus(
            status="ok" if all_ok else "degraded",
            details=combined_details,
        )
