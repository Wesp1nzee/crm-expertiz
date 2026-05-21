import time
from collections.abc import Awaitable, Callable
from typing import cast

from fastapi import HTTPException, Request, Response, status
from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from src.app.core.config import settings
from src.app.core.redis.redis import get_redis_client


class RateLimitExceededException(HTTPException):
    """Исключение при превышении лимита запросов"""

    def __init__(self, retry_after: int) -> None:
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Превышен лимит запросов. Попробуйте через {retry_after} секунд",
        )
        self.retry_after = retry_after


class RateLimiter:
    """Rate limiter на основе Redis (sliding window log)"""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self.prefix = "ratelimit:"

    async def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
        """
        Проверяет, разрешен ли запрос.

        Args:
            key: Уникальный ключ (например, IP пользователя)
            max_requests: Максимальное количество запросов в окне
            window_seconds: Размер окна в секундах

        Returns:
            Tuple[allowed, retry_after]: Разрешен ли запрос и время до следующего запроса
        """
        now = time.time()
        window_start = now - window_seconds
        rate_key = f"{self.prefix}{key}"

        async with self.redis.pipeline(transaction=True) as pipe:
            # Удаляем старые записи за пределами окна
            pipe.zremrangebyscore(rate_key, 0, window_start)
            # Подсчитываем количество запросов в текущем окне
            pipe.zcard(rate_key)
            # Добавляем текущий запрос
            pipe.zadd(rate_key, {f"{now}": now})
            # Устанавливаем TTL для ключа
            pipe.expire(rate_key, window_seconds + 10)
            results = await pipe.execute()

        request_count = cast(int, results[1])

        if request_count >= max_requests:
            # Вычисляем время до освобождения лимита
            oldest_in_window = await self.redis.zrange(rate_key, 0, 0, withscores=True)
            if oldest_in_window:
                oldest_timestamp = oldest_in_window[0][1]
                retry_after = int(oldest_timestamp + window_seconds - now) + 1
                return False, max(retry_after, 1)
            return False, window_seconds

        return True, 0

    async def check_rate_limit(self, key: str, max_requests: int, window_seconds: int) -> None:
        """
        Проверяет лимит и вызывает исключение при превышении.

        Raises:
            RateLimitExceededException: При превышении лимита
        """
        allowed, retry_after = await self.is_allowed(key, max_requests, window_seconds)
        if not allowed:
            raise RateLimitExceededException(retry_after)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Middleware для ограничения частоты запросов.

    Применяет rate limiting к определенным эндпоинтам (по умолчанию /api/users/login).
    """

    def __init__(
        self,
        app: ASGIApp,
        rate_limit_paths: list[str] | None = None,
        max_requests: int | None = None,
        window_seconds: int | None = None,
    ) -> None:
        super().__init__(app)
        self.rate_limit_paths = set(rate_limit_paths or ["/api/users/login"])
        self.max_requests = max_requests or settings.RATE_LIMIT_LOGIN_MAX_ATTEMPTS
        self.window_seconds = window_seconds or settings.RATE_LIMIT_LOGIN_WINDOW_SECONDS

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path not in self.rate_limit_paths:
            return await call_next(request)

        client_ip = (self._get_client_ip(request),)
        rate_limit_key = f"{request.method}:{request.url.path}:{client_ip}"

        try:
            redis_client = await get_redis_client()
            limiter = RateLimiter(redis_client)
            await limiter.check_rate_limit(rate_limit_key, self.max_requests, self.window_seconds)
        except RateLimitExceededException as e:
            return Response(
                status_code=e.status_code,
                content=str(e.detail),
                media_type="text/plain",
                headers={"Retry-After": str(e.retry_after)},
            )

        return await call_next(request)

    def _get_client_ip(self, request: Request) -> str:
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        return request.client.host if request.client else "unknown"
