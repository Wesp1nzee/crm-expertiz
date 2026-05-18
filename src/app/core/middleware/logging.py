import json
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

SENSITIVE_HEADERS = {"authorization", "cookie", "x-api-key"}
SENSITIVE_PATHS = {"/api/users/login", "/api/users/me", "/api/users/logout"}


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware для логирования запросов:
    - Время обработки (ms)
    - Метод, путь, статус-код
    - IP клиента, User-Agent
    - Опционально: тело запроса/ответа (для отладки)
    - Маскировка чувствительных данных
    - Пропуск health-check эндпоинтов
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        log_request_body: bool = False,
        log_response_body: bool = False,
        skip_paths: list[str] | None = None,
    ) -> None:
        super().__init__(app)
        self.log_request_body = log_request_body
        self.log_response_body = log_response_body
        self.skip_paths = set(skip_paths or ["/health", "/docs", "/openapi.json", "/redoc"])

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path in self.skip_paths:
            return await call_next(request)

        start_time = time.perf_counter()

        headers_log = {k.lower(): ("***" if k.lower() in SENSITIVE_HEADERS else v) for k, v in request.headers.items()}

        request_info = {
            "method": request.method,
            "path": request.url.path,
            "query_params": dict(request.query_params),
            "client_host": self._get_client_ip(request),
            "user_agent": headers_log.get("user-agent", "unknown"),
            "headers": headers_log if self.log_request_body else None,
        }

        if self.log_request_body and request.url.path not in SENSITIVE_PATHS:
            try:
                body = await request.body()
                if body:
                    request_info["body"] = body.decode("utf-8", errors="replace")[:1000]
            except Exception as e:
                request_info["body_error"] = str(e)

        logger.info(f"→ Request: {json.dumps(request_info, ensure_ascii=False)}")

        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                f"✗ Error | {request.method} {request.url.path} | {duration_ms:.2f}ms | {type(exc).__name__}: {exc}",
                exc_info=True,
            )
            raise

        duration_ms = (time.perf_counter() - start_time) * 1000
        response_info = {
            "status_code": response.status_code,
            "duration_ms": round(duration_ms, 2),
            "path": request.url.path,
            "method": request.method,
        }

        if self.log_response_body and request.url.path not in SENSITIVE_PATHS:
            try:
                body_parts: list[bytes] = []
                async for chunk in response.body_iterator:  # type: ignore[attr-defined]
                    body_parts.append(chunk)
                body = b"".join(body_parts)
                response_info["body"] = body.decode("utf-8", errors="replace")[:1000]

                response.body_iterator = self._async_iter(body_parts)  # type: ignore[attr-defined]
            except Exception as e:
                response_info["body_error"] = str(e)

        log_level = logging.WARNING if response.status_code >= 400 else logging.INFO
        logger.log(log_level, f"← Response: {json.dumps(response_info, ensure_ascii=False)}")

        response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"

        return response

    @staticmethod
    async def _async_iter(items: list[bytes]) -> AsyncGenerator[bytes]:
        """Вспомогательный метод для восстановления body_iterator"""
        for item in items:
            yield item

    def _get_client_ip(self, request: Request) -> str:
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        return request.client.host if request.client else "unknown"
