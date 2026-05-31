import json
import logging

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("api.exception")


def _extract_request_body(request: Request) -> str:
    """Best-effort extraction of the request body for error logging."""
    try:
        body = request._body  # noqa: SLF001
        if body:
            return body.decode("utf-8", errors="ignore")
    except Exception:
        pass
    return "<not-readable>"


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    request_id = request.headers.get("x-request-id", "unknown")

    body = _extract_request_body(request)

    error_details = {
        "request_id": request_id,
        "message": "Validation Failed (422)",
        "method": request.method,
        "path": request.url.path,
        "invalid_payload": body,
        "errors": exc.errors(),
    }

    logger.warning(f"✗ 422 Validation Error: {json.dumps(error_details, indent=2, ensure_ascii=False)}")

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": exc.errors(),
            "request_id": request_id,
        },
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    request_id = request.headers.get("x-request-id", "unknown")

    logger.warning(f"✗ HTTP {exc.status_code} | {request.method} {request.url.path} | ID: {request_id} | Detail: {exc.detail}")

    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "request_id": request_id},
    )


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = request.headers.get("x-request-id", "unknown")

    logger.error(
        f"500 ERROR | {request.method} {request.url.path} | ID: {request_id} | Error: {exc}",
        exc_info=True,
    )

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Internal Server Error. Please contact backend support.",
            "request_id": request_id,
        },
    )
