import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from src.app.core.config import settings
from src.app.core.database import all_models  # noqa: F401
from src.app.core.database.session import AsyncSessionLocal, engine
from src.app.core.middleware.logging import LoggingMiddleware
from src.app.core.redis import get_redis_client
from src.app.core.storage.s3 import s3_storage
from src.app.services.calendar.endpoints import router as calendar_router
from src.app.services.case.endpoints import router as cases_router
from src.app.services.client.endpoints import router as client_router
from src.app.services.company.endpoints import router as company_router
from src.app.services.document.endpoints import router as document_router
from src.app.services.mail.endpoints import router as mail_router
from src.app.services.mail.imap_worker import ImapFolderPoller, ImapIdleWorker
from src.app.services.share.endpoints import router as share_router
from src.app.services.user.endpoints import router as user_router
from src.app.services.user.setup import create_first_admin

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s")

logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    print("Starting system health checks...")

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        print("PostgreSQL connection: OK")
    except Exception as e:
        print(f"PostgreSQL connection: FAILED | {e}")
        raise e

    try:
        redis_client = await get_redis_client()
        await cast(Awaitable[Any], redis_client.ping())
        print("Redis connection: OK")
    except Exception as e:
        print(f"Redis connection: FAILED | {e}")

    try:
        await s3_storage.init_bucket()
        print("S3 Storage initialization: OK")
    except Exception as e:
        print(f"S3 Storage initialization: FAILED | {e}")

    try:
        async with AsyncSessionLocal() as session:
            await create_first_admin(session)
        print("Admin initialization check: OK")
    except Exception as e:
        print(f"Admin initialization: FAILED | {e}")

    imap_idle_worker = ImapIdleWorker(
        session_factory=AsyncSessionLocal,
        company_id=settings.MAIL_COMPANY_ID,
    )

    imap_folder_poller = ImapFolderPoller(
        session_factory=AsyncSessionLocal,
        company_id=settings.MAIL_COMPANY_ID,
    )

    idle_task = asyncio.create_task(imap_idle_worker.run(), name="imap-idle-inbox")
    poller_task = asyncio.create_task(imap_folder_poller.run(), name="imap-poller-folders")
    print("IMAP IDLE worker (INBOX): started")
    print("IMAP Folder poller (Sent/Drafts/Spam/Trash): started")
    print("Application is ready to serve requests.")
    yield

    print("Shutting down IMAP workers...")
    imap_idle_worker.stop()
    imap_folder_poller.stop()

    idle_task.cancel()
    poller_task.cancel()
    try:
        await asyncio.gather(idle_task, poller_task, return_exceptions=True)
    except Exception:
        pass

    print("Shutting down application...")
    await engine.dispose()
    print("Cleanup complete.")


app = FastAPI(title="CRM Expertiz API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    allow_headers=["Content-Type"],
)

app.add_middleware(
    LoggingMiddleware,
    log_request_body=False,
    log_response_body=False,
    skip_paths=["/health", "/docs", "/openapi.json", "/redoc"],
)
app.include_router(cases_router)
app.include_router(client_router)
app.include_router(document_router)
app.include_router(user_router)
app.include_router(company_router)
app.include_router(calendar_router)
app.include_router(share_router)
app.include_router(mail_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
