import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) -> %(message)s",
)
logger = logging.getLogger("api")

SENSITIVE_HEADERS = {"authorization", "cookie", "x-api-key"}
SENSITIVE_PATHS = {"/api/users/login", "/api/users/me", "/api/users/logout"}


class LoggingMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        *,
        log_request_body: bool = False,
        skip_paths: list[str] | None = None,
    ) -> None:
        super().__init__(app)
        self.log_request_body = log_request_body
        self.skip_paths = set(skip_paths or ["/health", "/docs", "/openapi.json", "/redoc"])

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path in self.skip_paths:
            return await call_next(request)

        start_time = time.perf_counter()

        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))

        headers_log = {k.lower(): ("***" if k.lower() in SENSITIVE_HEADERS else v) for k, v in request.headers.items()}

        request_info = {
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "query_params": dict(request.query_params),
            "client_host": self._get_client_ip(request),
            "user_agent": headers_log.get("user-agent", "unknown"),
        }

        if self.log_request_body and request.url.path not in SENSITIVE_PATHS:
            try:
                body = await request.body()
                if body:
                    request_info["body"] = json.loads(body.decode("utf-8"))
            except Exception as e:
                request_info["body_error"] = f"Could not parse body: {e}"

        logger.info(f"→ Request: {json.dumps(request_info, ensure_ascii=False)}")

        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                f"✗ Critical Middleware Error | {request.method} {request.url.path} | "
                f"ID: {request_id} | {duration_ms:.2f}ms | {type(exc).__name__}: {exc}",
                exc_info=True,
            )
            raise

        duration_ms = (time.perf_counter() - start_time) * 1000

        response_info = {
            "request_id": request_id,
            "status_code": response.status_code,
            "duration_ms": round(duration_ms, 2),
            "path": request.url.path,
            "method": request.method,
        }

        if response.status_code >= 500:
            log_level = logging.ERROR
        elif response.status_code >= 400:
            log_level = logging.WARNING
        else:
            log_level = logging.INFO

        logger.log(log_level, f"← Response: {json.dumps(response_info, ensure_ascii=False)}")

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"

        return response

    def _get_client_ip(self, request: Request) -> str:
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        return request.client.host if request.client else "unknown"
